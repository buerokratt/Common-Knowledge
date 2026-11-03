"""The marker that tells F21's probe "that content is our own bootstrap".

THE PROBLEM IT SOLVES. F21 refuses a `first_run` against a destination that
already holds the agency's content: in a mature deployment a missing manifest
means something deleted it, and a cold start would republish the corpus and
permanently orphan every document CKB deleted while the manifest was gone.
But a bootstrap killed before its first checkpoint (F15 commits every
BOOTSTRAP_CHECKPOINT_EVERY published documents) leaves exactly that state too
— content in the destination, no manifest — and refusing it would wedge the
deployment on every tick until an operator stepped in.

THE MARKER. A first run that passes the probe writes
`{CONTENT_WORK_DIR}/_bootstrap/{agency_id}.inprogress` before it publishes
anything, and the first commit — a checkpoint or the final one — removes it.
Should that removal fail, every later run that finds a manifest removes it
before doing anything else, and fails if it cannot: a marker must never
outlive the manifest, or a manifest lost months later would meet a marker
that vouches for content it never saw. So the marker exists only while this
service has published content that no manifest records. A run under
--accept-existing-content never writes one: the content it publishes into is
not all this service's own. Present at the start of a later first run, it means the
content is ours and the bootstrap resumes; Gate 2 then spares nothing, but
every upsert is an overwrite of the same coordinates, so nothing duplicates.

WHY THE FAILURE MODES ARE SAFE. The marker lives on the same durable volume
as the run lock and the deletion journal. If that volume is lost, the marker
is lost with it and the probe refuses: the safe direction, and the one the
design wants a lost volume to take. A marker for another sink is ignored, so
switching CONTENT_SINK mid-bootstrap cannot vouch for content in the new
destination. A marker that cannot be parsed is ignored too, never trusted.
"""

import json
import logging
import os
import tempfile
from contextlib import suppress
from pathlib import Path

from exporter.core.ids import validate_segment
from exporter.services.run_state import utc_now_iso

__all__ = ["BOOTSTRAP_DIRNAME", "BootstrapMarker"]

logger = logging.getLogger(__name__)

BOOTSTRAP_DIRNAME = "_bootstrap"

_SUFFIX = ".inprogress"


class BootstrapMarker:
    """One agency's in-progress-bootstrap marker, for one sink."""

    def __init__(self, work_dir: Path, *, agency_id: str, sink_id: str) -> None:
        # A path segment: an agency id that escaped its folder would put the
        # marker somewhere no later run looks.
        validate_segment("agency_id", agency_id)
        self._path = work_dir / BOOTSTRAP_DIRNAME / f"{agency_id}{_SUFFIX}"
        self._agency_id = agency_id
        self._sink_id = sink_id

    @property
    def path(self) -> Path:
        return self._path

    def present(self) -> bool:
        """True when a marker for this agency AND this sink is on disk."""
        return self.read() is not None

    def read(self) -> dict[str, object] | None:
        """The marker's record (agency_id, sink_id, run_id, created_at) when
        one for this agency AND this sink is on disk, else None — absent,
        unreadable or written for another sink alike."""
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            logger.warning(
                "the bootstrap marker at %s cannot be read; ignoring it", self._path
            )
            return None
        if not isinstance(raw, dict):
            logger.warning("the bootstrap marker at %s is not an object", self._path)
            return None
        if (
            raw.get("agency_id") != self._agency_id
            or raw.get("sink_id") != self._sink_id
        ):
            logger.warning(
                "the bootstrap marker at %s was written for another agency or "
                "sink; ignoring it",
                self._path,
            )
            return None
        return raw

    def create(self, run_id: str) -> None:
        """Write the marker durably, before the first upsert. Atomic replace,
        so a crash leaves the old marker or the new one, never half of one."""
        directory = self._path.parent
        directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "agency_id": self._agency_id,
                "sink_id": self._sink_id,
                "run_id": run_id,
                "created_at": utc_now_iso(),
            },
            sort_keys=True,
        )
        fd, tmp = tempfile.mkstemp(prefix=f".{self._path.name}.", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self._path)
        except BaseException:
            with suppress(OSError):
                Path(tmp).unlink(missing_ok=True)
            raise
        _fsync_directory(directory)

    def clear(self) -> None:
        """Remove the marker. Idempotent: absent is the normal case."""
        self._path.unlink(missing_ok=True)


def _fsync_directory(directory: Path) -> None:
    """POSIX only, as in services/deletion_journal.py: Windows cannot open a
    directory for fsync, and the service only ships in a Linux container."""
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
