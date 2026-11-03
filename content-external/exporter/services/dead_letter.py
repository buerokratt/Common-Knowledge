"""The dead-letter record — documents stuck on a fault of their own (E9, F5).

WHAT IS DEAD-LETTERED. A document skipped because of something wrong with the
document itself, which no amount of waiting will fix:

- a read-time guard (E9, C5): content or sidecar over its size limit,
  undecodable bytes, a sidecar that does not parse, an invalid object key, a
  missing or uncitable source_url, a content object that is not there;
- MAX_CHUNKS_PER_DOCUMENT, which can only fire after chunking.

Not dead-lettered, because each is expected or someone else's fault: a row
that is not ready (`in_review` can last days), a --limit skip, an uploaded
file skipped by policy at enumeration, and a destination that did not confirm
an upsert (F18 reports those as publish_failures).

WHY ONE RECORD AND NOT A LOG. These documents are read again on every run —
their verdict depends on configuration and content, so they withhold the
corpus watermark (publish_service.py) — and each run sees the same ones. A
per-run file would repeat the same entries every hour. What an operator needs
is the current set and how long each has been stuck, so this is one JSON file
per agency, `{CONTENT_WORK_DIR}/_dead_letter/{agency_id}.json`, rewritten after
each committed run:

- a run that observed the whole corpus (F20) replaces the set with its own;
- a partial run (--limit) adds what it dead-lettered and removes only what it
  resolved, because it cannot vouch for the rows it did not read;
- a failed run does not touch it: it committed nothing to conclude from.

`first_seen_at` survives across runs, so "stuck for three weeks" is visible.

OBSERVABILITY ONLY, LIKE run_state.py. Written after the commit, atomically
(temp file + os.replace) and best effort: a record that cannot be written or
read is logged and never fails an export. It carries ids, reason codes and
timestamps only, never text or a URL (F13). Losing the volume loses nothing:
the next complete run rebuilds it from the documents still stuck.
"""

import json
import logging
import os
import tempfile
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path

from exporter.core.ids import validate_segment

__all__ = ["DEAD_LETTER_DIRNAME", "DeadLetterEntry", "DeadLetterStore"]

logger = logging.getLogger(__name__)

DEAD_LETTER_DIRNAME = "_dead_letter"


@dataclass(frozen=True, slots=True)
class DeadLetterEntry:
    reason: str
    first_seen_at: str
    last_seen_at: str
    last_run_id: str
    # Committed runs in a row that found it stuck.
    runs: int


class DeadLetterStore:
    """The current dead-letter record for one agency."""

    def __init__(self, work_dir: Path, *, agency_id: str) -> None:
        # The agency id becomes a file name.
        validate_segment("agency_id", agency_id)
        self._agency_id = agency_id
        self._path = work_dir / DEAD_LETTER_DIRNAME / f"{agency_id}.json"

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, DeadLetterEntry]:
        """The current record, or empty. Never raises: an unreadable record
        warns and reads as empty, which the next complete run rewrites."""
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            logger.warning(
                "could not read the dead-letter record at %s; treating it as empty",
                self._path,
            )
            return {}
        try:
            if raw["agency_id"] != self._agency_id:
                raise ValueError("another agency's record")
            return {
                str(document_id): DeadLetterEntry(
                    reason=str(entry["reason"]),
                    first_seen_at=str(entry["first_seen_at"]),
                    last_seen_at=str(entry["last_seen_at"]),
                    last_run_id=str(entry["last_run_id"]),
                    runs=int(entry["runs"]),
                )
                for document_id, entry in raw["documents"].items()
            }
        except (KeyError, TypeError, ValueError, AttributeError):
            logger.warning(
                "the dead-letter record at %s is malformed; treating it as empty",
                self._path,
            )
            return {}

    def update(
        self,
        dead: Mapping[str, str],
        *,
        resolved: Iterable[str],
        complete: bool,
        run_id: str,
        at: str,
    ) -> dict[str, DeadLetterEntry]:
        """Apply one committed run and return the new record.

        `dead` is this run's dead-lettered documents and their reasons.
        `resolved` is the documents this run brought to a definite outcome —
        published, unchanged or deleted — which leave the record even on a
        partial run. With `complete`, everything not in `dead` leaves.
        """
        previous = self.load()
        kept = {} if complete else dict(previous)
        for document_id in resolved:
            kept.pop(document_id, None)
        for document_id, reason in dead.items():
            before = previous.get(document_id)
            if before is None:
                logger.warning("document %s dead-lettered: %s", document_id, reason)
            kept[document_id] = DeadLetterEntry(
                reason=reason,
                first_seen_at=at if before is None else before.first_seen_at,
                last_seen_at=at,
                last_run_id=run_id,
                runs=1 if before is None else before.runs + 1,
            )
        for document_id in previous.keys() - kept.keys():
            logger.info("document %s left the dead-letter record", document_id)
        self._write(kept, run_id=run_id, at=at)
        return kept

    def _write(
        self, documents: Mapping[str, DeadLetterEntry], *, run_id: str, at: str
    ) -> None:
        payload = {
            "agency_id": self._agency_id,
            "updated_at": at,
            "updated_by_run": run_id,
            "documents": {
                document_id: asdict(entry)
                for document_id, entry in sorted(documents.items())
            },
        }
        tmp: str | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                prefix=f"{self._path.name}.", suffix=".tmp", dir=self._path.parent
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2)
                )
            os.replace(tmp, self._path)
        except OSError:
            logger.warning(
                "could not write the dead-letter record to %s; it is rebuilt by "
                "the next complete run",
                self._path,
            )
            if tmp is not None:
                with suppress(OSError):
                    Path(tmp).unlink(missing_ok=True)
