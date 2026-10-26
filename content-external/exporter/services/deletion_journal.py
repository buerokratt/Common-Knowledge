"""The deferred-deletion journal (F7, Key Design #7 point 5).

WHERE AND WHY THERE. `{CONTENT_WORK_DIR}/_deferred/{run_id}.jsonl`, on the
service's own durable volume. When the destination is what is broken, the
incident record cannot be written to the destination — and on the llm-module
sink there is nowhere to write it at all. So the journal of record is local,
and it is copied to the manifest store only after the run commits ("mirrored
on success"). A tmpfs or per-run directory would silently destroy the only
record of a delete attempted while the destination was down; that is why
CONTENT_WORK_DIR is a named volume / PVC, and why rule 1 keeps it out of
/scrapped-data (asserted at startup in api/config.py).

WRITE-AHEAD. Every delete is journalled as an `intent` line, flushed and
fsynced, BEFORE the sink is asked to delete anything, and an `outcome` line
follows once the sink answers. A run killed between the two leaves an intent
with no outcome, which is exactly the case a drain has to see. Any OSError
propagates: the manifest is about to forget every deleted document, and if
this record cannot be written the run must commit nothing.

TORN LINES. A line that does not parse is a write a crash cut short, and it is
skipped wherever it sits. That is safe because every line is fsynced before
what it records happens: a torn intent means the sink was never asked, a torn
outcome leaves its intent pending, and a torn `closed` leaves the journal open.
The next append terminates a torn tail first, so it never swallows a new line.

CLOSED. After a run commits, it appends a `closed` line (per agency) to its
own journal and to every earlier journal it reconciled. An intent in a journal
that was never closed belongs to a run that died before its commit, so the
committed manifest may still list a document whose content that run already
deleted; the next export finds those journals (`agency_journals`) and
republishes such documents in full before closing them. `closed` says the
MANIFEST has caught up with the journal — not that every delete in it landed.

THE CONTRACT FOR THE DRAIN (J1), stated here because this format is what it
reads. A pending item is an intent with no outcome, or an outcome that lists it
as unresolved, whether or not the journal is closed. Lines whose event is
`closed` carry no document and are skipped. Before deleting one, the drain must
re-check it against the CURRENT committed manifest: skip a document that is in
the manifest again (it was restored and re-published since), and skip any
chunk ordinal below that document's current `chunk_count` (it was re-published
since). Without that check, a drain that runs after a later export deletes live
content — the sequential form of flagged risk 8, which the run lock alone does
not prevent. Tail (`chunks`) entries are informational besides: diff.record()
keeps a document whose tail was not confirmed on its previous entry, so the
next export recomputes and retries that tail by itself.

MIRRORED. `{run_id}.mirrored` beside a journal holds the byte length last
copied to the manifest store. A closed journal whose length differs — never
mirrored, a mirror that failed, or a `closed` line appended since — is copied
again after the next commit, so a lost mirror is retried rather than forgotten.

WHAT A LINE HOLDS. Ids, ordinals, reason codes and timestamps. Never text,
never a URL, never a request or response body (F13). One JSON object per
line, keys sorted, so two runs diff line for line.
"""

import json
import logging
import os
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any

from exporter.core.ids import validate_segment
from exporter.services.run_state import utc_now_iso
from exporter.stores.base import DeleteOutcome

__all__ = [
    "CLOSED_EVENT",
    "JOURNAL_DIRNAME",
    "TAIL_REASON",
    "DeletionJournal",
    "DeletionKind",
    "agency_journals",
]

logger = logging.getLogger(__name__)

JOURNAL_DIRNAME = "_deferred"

# The reason on a tail delete: the ordinals a re-chunked document no longer
# has (C16).
TAIL_REASON = "tail"

# The line a committed run appends once the manifest has caught up with a
# journal; see CLOSED in the module docstring.
CLOSED_EVENT = "closed"

_INTENT_EVENT = "intent"
_JOURNAL_SUFFIX = ".jsonl"
_MIRRORED_SUFFIX = ".mirrored"


class DeletionKind(StrEnum):
    DOCUMENT = "document"  # a whole document: chunks, then its record (F4)
    CHUNKS = "chunks"  # tail ordinals of a document that shrank


class DeletionJournal:
    """One run's journal. The file is created on the first intent, so a run
    that deletes nothing writes nothing."""

    def __init__(
        self,
        work_dir: Path,
        *,
        run_id: str,
        agency_id: str,
        clock: Callable[[], str] = utc_now_iso,
    ) -> None:
        # The run id becomes a file name here and a key segment in the mirror,
        # so it is held to the key builders' rule before either exists.
        validate_segment("run_id", run_id)
        self._path = work_dir / JOURNAL_DIRNAME / f"{run_id}{_JOURNAL_SUFFIX}"
        self._run_id = run_id
        self._agency_id = agency_id
        self._clock = clock
        self._intents = 0

    @property
    def path(self) -> Path:
        return self._path

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def intents_recorded(self) -> int:
        """Delete intents this run journalled — RunReport.deletions_recorded."""
        return self._intents

    # -- reconciliation (CLOSED, MIRRORED) ---------------------------------

    def is_closed(self) -> bool:
        """Whether a committed run has closed this journal for this agency."""
        return any(
            entry.get("event") == CLOSED_EVENT
            and entry.get("agency_id") == self._agency_id
            for entry in self.entries()
        )

    def attempted_documents(self) -> frozenset[str]:
        """Every document this agency's intents name, whole or tail: what the
        destination may no longer hold as the manifest last described it."""
        return frozenset(
            str(entry["document_id"])
            for entry in self.entries()
            if entry.get("event") == _INTENT_EVENT
            and entry.get("agency_id") == self._agency_id
            and "document_id" in entry
        )

    def close(self, *, closed_by: str) -> None:
        """Record that the committed run `closed_by` has reconciled the
        manifest with this journal. Nothing to close when no file exists."""
        if not self._path.exists():
            return
        self._append(
            [
                {
                    "event": CLOSED_EVENT,
                    "run_id": self._run_id,
                    "agency_id": self._agency_id,
                    "closed_by": closed_by,
                    "recorded_at": self._clock(),
                }
            ]
        )

    def needs_mirror(self) -> bool:
        """A closed journal not yet mirrored at its current length."""
        payload = self.payload()
        if payload is None or not self.is_closed():
            return False
        return self._mirrored_length() != len(payload)

    def mark_mirrored(self, payload: bytes) -> None:
        """Record that `payload` reached the manifest store. Best-effort: a
        marker that is not written only costs the next run a repeat PUT."""
        try:
            self._marker_path.write_text(str(len(payload)), encoding="utf-8")
        except OSError:
            logger.warning("could not record the mirror of %s", self._path)

    @property
    def _marker_path(self) -> Path:
        return self._path.with_suffix(_MIRRORED_SUFFIX)

    def _mirrored_length(self) -> int | None:
        try:
            return int(self._marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    # -- documents ---------------------------------------------------------

    def intend_document_deletes(self, reasons: Mapping[str, str]) -> None:
        """Journal whole-document deletes, keyed document_id -> reason, before
        the sink is called. One fsync for the batch."""
        self._append(
            self._line(_INTENT_EVENT, DeletionKind.DOCUMENT, document_id, reason=reason)
            for document_id, reason in reasons.items()
        )
        self._intents += len(reasons)

    def document_deletes_done(self, outcome: DeleteOutcome[str]) -> None:
        """Journal what the sink did with each document, one line each."""
        items = (*outcome.succeeded, *outcome.unresolved)
        self._append(
            self._line(
                "outcome",
                DeletionKind.DOCUMENT,
                document_id,
                **_resolution(outcome, only=document_id),
            )
            for document_id in items
        )

    # -- chunk tails -------------------------------------------------------

    def intend_chunk_delete(self, document_id: str, ordinals: Sequence[int]) -> None:
        """Journal a tail delete before the sink is called."""
        self._append(
            [
                self._line(
                    _INTENT_EVENT,
                    DeletionKind.CHUNKS,
                    document_id,
                    reason=TAIL_REASON,
                    ordinals=sorted(ordinals),
                )
            ]
        )
        self._intents += 1

    def chunk_delete_done(self, document_id: str, outcome: DeleteOutcome[int]) -> None:
        """Journal what the sink did with a tail, one line for all of it."""
        self._append(
            [
                self._line(
                    "outcome", DeletionKind.CHUNKS, document_id, **_resolution(outcome)
                )
            ]
        )

    # -- reading -----------------------------------------------------------

    def payload(self) -> bytes | None:
        """The journal's bytes, for the mirror; None when nothing was
        journalled."""
        try:
            return self._path.read_bytes()
        except FileNotFoundError:
            return None

    def entries(self) -> list[dict[str, Any]]:
        """Every line, parsed, in the order written. A line that does not
        parse is skipped; see TORN LINES in the module docstring."""
        payload = self.payload()
        if payload is None:
            return []
        parsed: list[dict[str, Any]] = []
        for line in payload.split(b"\n"):
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                entry = None
            if not isinstance(entry, dict):
                logger.warning("skipping a torn line in %s", self._path)
                continue
            parsed.append(entry)
        return parsed

    # -- internals ---------------------------------------------------------

    def _line(
        self, event: str, kind: DeletionKind, document_id: str, **fields: object
    ) -> dict[str, object]:
        return {
            "event": event,
            "kind": kind.value,
            "run_id": self._run_id,
            "agency_id": self._agency_id,
            "document_id": document_id,
            "recorded_at": self._clock(),
            **fields,
        }

    def _append(self, lines: Iterable[dict[str, object]]) -> None:
        data = "".join(
            json.dumps(line, sort_keys=True, ensure_ascii=False) + "\n"
            for line in lines
        ).encode("utf-8")
        if not data:
            return
        directory = self._path.parent
        created_directory = not directory.exists()
        directory.mkdir(parents=True, exist_ok=True)
        created_file = not self._path.exists()
        with self._path.open("a+b") as handle:
            if handle.seek(0, os.SEEK_END) > 0:
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    # A torn tail from a crash: terminate it, or this line
                    # would be glued onto it and lost with it.
                    data = b"\n" + data
            handle.write(data)
            handle.flush()
            # Durable before the sink is called, not merely buffered: a crash
            # after the delete but before the OS flushed would lose the record
            # of a delete that may have half-happened.
            os.fsync(handle.fileno())
        # A new file is not durable until its directory entry is, and the
        # same goes for a new directory.
        if created_directory:
            _fsync_directory(directory.parent)
        if created_file:
            _fsync_directory(directory)


def agency_journals(work_dir: Path, agency_id: str) -> list[DeletionJournal]:
    """Every journal on disk with a line for `agency_id`, oldest run id
    first. A file whose name is not a valid run id was not written here and is
    skipped. Read errors propagate: a run that cannot see what earlier runs
    deleted cannot know which manifest entries to trust."""
    journals: list[DeletionJournal] = []
    for path in sorted((work_dir / JOURNAL_DIRNAME).glob(f"*{_JOURNAL_SUFFIX}")):
        try:
            journal = DeletionJournal(work_dir, run_id=path.stem, agency_id=agency_id)
        except ValueError:
            logger.warning("skipping %s: its name is not a run id", path)
            continue
        if any(entry.get("agency_id") == agency_id for entry in journal.entries()):
            journals.append(journal)
    return journals


def _fsync_directory(directory: Path) -> None:
    """POSIX only, as in stores/local_store.py: Windows cannot open a
    directory for fsync, and the service only ships in a Linux container."""
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _resolution[T: Hashable](
    outcome: DeleteOutcome[T], *, only: T | None = None
) -> dict[str, object]:
    """The fate of every item in `outcome` — or of `only` — as journal fields.
    Deferred and failed are both unresolved, which is what a drain retries,
    with the reason kept apart."""

    def wanted(item: T) -> bool:
        return only is None or item == only

    unresolved: list[dict[str, object]] = [
        {"item": item, "reason": "deferred"}
        for item in outcome.deferred
        if wanted(item)
    ]
    unresolved.extend(
        {"item": item, "reason": reason}
        for item, reason in outcome.failed.items()
        if wanted(item)
    )
    return {
        "resolved": [item for item in outcome.succeeded if wanted(item)],
        "unresolved": unresolved,
    }
