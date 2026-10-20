"""The publish coordinator â€” the one stateful component (Stage F, F2â€“F7).
Key Designs #5 and #7.

ORDERING IS THE DELIVERABLE, AND MOST OF IT IS NOT HERE. "No chunk is
retrievable while its document's record is missing" (F3) and "on delete,
content goes before the record" (F4) are guarantees of the ContentSink
contract. The object-store sink meets them by write order and the llm-module
sink by one request per document. The sink conformance suite (D15) asserts
them per implementation. This module calls `upsert_document(record, chunks)`
and `delete_documents(ids)` and never sequences a destination's writes itself,
which is why it holds a ContentSink and a manifest service â€” never a store
client, never a transport â€” and never asks which sink it holds (A13).

WHAT THIS MODULE DOES ORDER, across documents and across the run:

    take the run lock (F2)  â€” held: record `busy`, touch nothing else
    read the corpus watermark, then load the previous manifest (F6, F17)
    distrust every entry an uncommitted run's journal touched (F7)
    enumerate the rows; per row, streamed (E11):
        read it only if the diff says it must (Gate 2)
        classify it, then dispatch by bucket (F5):
            new, content_changed  -> chunk, upsert_document(record, chunks)
                                     then journal + delete_chunks(tail)
            metadata_changed      -> upsert_document(record, None),
                                     or a full re-push where the sink cannot
                                     merge (L8), counted
            unchanged, skipped    -> nothing
            deleted               -> collected
    journal, then delete_documents(every deleted id) (F4, F7)
    build the next manifest with diff.record(), then COMMIT it (F6)
    close the journals it reconciled, and mirror the unmirrored ones (F7)

THE COMMIT IS THE ONLY POINT OF NO RETURN, and any exception before it fails
the run with nothing committed; the next tick is the retry. An upsert before it
is replayable â€” chunk ids are coordinates, so a replay overwrites rather than
adds â€” provided the row still moved. A delete is not replayable on its own: a
run that deleted a document (or a tail) and then died leaves a manifest that
still describes content the destination no longer holds, and if the row is
restored with the same content the diff would call it unchanged. So every
entry named in a journal no committed run has closed is distrusted before
classification: its fingerprint becomes UNCONFIRMED_FINGERPRINT, which reads
and republishes it in full, or deletes it again if its row is still deleted.

A document the destination did not confirm is not an exception: it keeps its
previous manifest entry, is counted, and is retried next run, and the corpus
watermark is withheld so the next run cannot skip it. Neither is a document
whose re-chunk disagrees with its published count: it is held, marked
UNCONFIRMED_FINGERPRINT, and republished in full next run.

NOT YET HERE, deliberately: Gate 1 (E4), backoff (F8), bootstrap checkpoints
(F15), concurrency (F16 â€” the loop is sequential, so the manifest's single
writer is trivially this one), per-document report detail (F18), the
systemic-failure abort (F19), `run_observed_full_corpus` (F20) and the
non-empty-destination probe (F21).

REDACTION (F13). Log lines carry ids, counts and sanitised reason codes. Never
document text, chunk text, a URL or a request/response body, and an exception
is logged by type and sanitised message â€” never with a traceback, whose frames
could hold a document.
"""

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import cast

from exporter.core.chunking import Chunker, chunk_document
from exporter.core.diff import (
    Bucket,
    DeletionReason,
    DiffResult,
    DocumentDiff,
    classify_document,
    needs_read,
    record,
    tail_coordinates,
)
from exporter.core.metadata import build_document_record
from exporter.core.redaction import sanitize_sensitive_text
from exporter.core.schemas import (
    DocumentRecord,
    DocumentRef,
    Manifest,
    ManifestDocumentEntry,
    ParsedSidecar,
    RunReport,
)
from exporter.services.deletion_journal import DeletionJournal, agency_journals
from exporter.services.document_source import DocumentSource, ReadDocument
from exporter.services.locking import run_lock
from exporter.services.manifest_service import ManifestService
from exporter.services.run_log import log_run_outcome
from exporter.services.run_state import RunOutcome, RunStateStore, utc_now_iso
from exporter.sinks.base import ContentSink, UpsertOutcome

__all__ = [
    "TOO_MANY_CHUNKS",
    "UNCONFIRMED_FINGERPRINT",
    "EmptyEnumerationError",
    "PublishCoordinator",
]

logger = logging.getLogger(__name__)

# The skip reason for a document over MAX_CHUNKS_PER_DOCUMENT, a guard that
# can only run after chunking (E9; DiffResult.demote_to_skipped).
TOO_MANY_CHUNKS = "too_many_chunks"

# A chunker fingerprint no geometry has: a SHA-256 is never all zeros. An
# entry carries it when the coordinator cannot vouch for the document's live
# chunks, so the diff reads it and republishes it in full however unchanged
# its row looks, exactly as after a real fingerprint change. Its chunk_count
# stays the last one published, so the tail is still computed from it.
UNCONFIRMED_FINGERPRINT = "0" * 64

_PUBLISH_BUCKETS = frozenset(
    {Bucket.NEW, Bucket.CONTENT_CHANGED, Bucket.METADATA_CHANGED}
)


class EmptyEnumerationError(RuntimeError):
    """The enumeration returned no rows for an agency the manifest says has
    published documents.

    The rows include deleted and excluded ones (E3), so a real agency never
    enumerates empty: this is a broken query, a wrong agency or a restored
    database, and acting on it would delete the whole published corpus as
    "absent from the rows". Deleting everything is run_purge's job (J7), on an
    operator's explicit request, never an export's side effect.
    """


@dataclass
class _Progress:
    """What the per-row loop learned, for the diff, the manifest and the
    report. Mutable, and private to one run."""

    # The run's start: every record published this run carries it as
    # synced_at, and every manifest entry it writes as processed_at.
    synced_at: str
    changes: list[DocumentDiff | None] = field(default_factory=list)
    published: set[str] = field(default_factory=set)
    publish_failed: set[str] = field(default_factory=set)
    chunk_counts: dict[str, int] = field(default_factory=dict)
    tails_deleted: set[str] = field(default_factory=set)
    tails_unresolved: set[str] = field(default_factory=set)
    demoted: dict[str, str] = field(default_factory=dict)
    # Skipped by a read-time guard (oversize, a rejected sidecar). Like a
    # demotion, the verdict depends on configuration rather than on the row,
    # so it withholds the watermark: a raised limit must not meet a Gate 1
    # that skips the unchanged corpus.
    read_skipped: set[str] = field(default_factory=set)
    # Held because their published chunks could not be vouched for; marked
    # UNCONFIRMED_FINGERPRINT in the next manifest.
    unconfirmed: set[str] = field(default_factory=set)
    deletions_unresolved: int = 0
    metadata_full_repush: int = 0
    source_url_fallback: int = 0
    source_url_mismatch: int = 0


class PublishCoordinator:
    """Exports one agency to one sink. Built per run: the sink is bound to
    the agency (sinks/base.py), so the coordinator is too."""

    def __init__(
        self,
        *,
        sink: ContentSink,
        manifests: ManifestService,
        source: DocumentSource,
        chunker: Chunker,
        chunker_fingerprint: str,
        work_dir: Path,
        max_chunks_per_document: int,
        run_state: RunStateStore | None = None,
        clock: Callable[[], str] = utc_now_iso,
    ) -> None:
        if manifests.sink_id != sink.sink_id:
            raise ValueError(
                "the manifest service and the sink name different destinations"
            )
        if max_chunks_per_document < 1:
            raise ValueError("max_chunks_per_document must be >= 1")
        self._sink = sink
        self._manifests = manifests
        self._source = source
        self._chunker = chunker
        self._fingerprint = chunker_fingerprint
        self._work_dir = work_dir
        self._max_chunks = max_chunks_per_document
        self._run_state = run_state
        self._clock = clock

    @property
    def agency_id(self) -> str:
        return self._sink.agency_id

    def export(self, run_id: str) -> RunReport:
        """Run one export and return its report. An Exception, including a
        lock file that cannot be opened, is the report's `failed` outcome
        rather than a raise, and the next tick is the retry. Only a
        BaseException (SystemExit, KeyboardInterrupt) escapes."""
        started_at = self._clock()
        started = time.monotonic()
        report: RunReport | None = None
        try:
            with run_lock(self._work_dir) as acquired:
                if acquired:
                    report = self._attempt(run_id, started_at)
                    # Recorded while the lock is still held: once it is free
                    # the next run may start, and its record must not race
                    # this one's. A `busy` can still interleave; it is only
                    # a counter.
                    self._observe(report, time.monotonic() - started)
                    return report
        except OSError as exc:
            if report is not None:
                # Releasing failed after a run that finished and was recorded;
                # closing the descriptor released the lock regardless.
                return report
            logger.error(
                "run %s: the run lock could not be taken, nothing ran: %s: %s",
                run_id,
                type(exc).__name__,
                sanitize_sensitive_text(str(exc)),
            )
            report = self._report(run_id, "failed", started_at)
        else:
            # Reached only when the lock was not acquired: the acquired branch
            # returns from inside it.
            logger.info("run %s: another export or drain holds the lock", run_id)
            report = self._report(run_id, "busy", started_at)
        self._observe(report, time.monotonic() - started)
        return report

    def _attempt(self, run_id: str, started_at: str) -> RunReport:
        try:
            return self._run(run_id, started_at)
        except Exception as exc:
            # Every failure is `failed`, and nothing was committed. Type and
            # sanitised message only, never a traceback; see the module
            # docstring.
            logger.error(
                "run %s failed, nothing committed: %s: %s",
                run_id,
                type(exc).__name__,
                sanitize_sensitive_text(str(exc)),
            )
            return self._report(run_id, "failed", started_at)

    # -- the run -----------------------------------------------------------

    def _run(self, run_id: str, started_at: str) -> RunReport:
        agency_id = self.agency_id
        journal = DeletionJournal(self._work_dir, run_id=run_id, agency_id=agency_id)
        if journal.is_closed():
            # Appending to it would merge two runs' records under one id, and
            # the mirror would publish them as one.
            raise ValueError(
                f"run id {run_id} already committed for this agency; a new run "
                "needs a new id"
            )

        # The watermark before the rows: a row that moves in between makes the
        # recorded watermark older than what was read, which only costs the
        # next run a walk. The other order could claim a watermark for a row
        # this run never saw.
        watermark = self._source.watermark(agency_id)
        previous = self._manifests.load_previous(agency_id)
        on_disk = agency_journals(self._work_dir, agency_id)
        unreconciled = [j for j in on_disk if not j.is_closed()]
        entries = _distrust(
            {} if previous is None else previous.documents, unreconciled
        )
        rows = self._source.list_documents(agency_id)
        if not rows and entries:
            raise EmptyEnumerationError(
                f"agency {agency_id}: the enumeration returned no rows, but "
                f"{len(entries)} document(s) are published; refusing to delete "
                "them all as absent. Use run_purge to delete an agency."
            )

        # Every row is classified, and its document published, before any
        # whole document is deleted.
        progress = _Progress(synced_at=started_at)
        seen: set[str] = set()
        for row in rows:
            self._check_row(row, seen)
            progress.changes.append(
                self._publish_one(row, entries.get(row.document_id), progress, journal)
            )
        progress.changes.extend(
            DocumentDiff(
                document_id,
                Bucket.DELETED,
                DeletionReason.ABSENT_FROM_ROWS,
                None,
                entries[document_id],
            )
            for document_id in sorted(set(entries) - seen)
        )
        self._delete_documents(progress, journal)

        diff = self._assemble(previous, progress)
        documents = record(
            diff,
            progress.published,
            tails_deleted=progress.tails_deleted,
            processed_at=progress.synced_at,
        )
        for document_id in documents.keys() & progress.unconfirmed:
            documents[document_id] = replace(
                documents[document_id], chunker_fingerprint=UNCONFIRMED_FINGERPRINT
            )
        manifest = self._manifests.build(
            agency_id=agency_id,
            diff=diff,
            documents=documents,
            watermark=watermark,
            previous=previous,
            advance_watermark=not (
                progress.publish_failed
                or progress.tails_unresolved
                or progress.demoted
                or progress.read_skipped
                or any(
                    entry.chunker_fingerprint == UNCONFIRMED_FINGERPRINT
                    for entry in documents.values()
                )
            ),
            committed_at=self._clock(),
        )
        self._manifests.commit(manifest)  # F6: the commit point.
        self._reconcile_journals(run_id, [*unreconciled, journal], [*on_disk, journal])

        return replace(
            self._report(run_id, "success", started_at),
            new_count=len(diff.new),
            content_changed_count=len(diff.content_changed),
            metadata_changed_count=len(diff.metadata_changed),
            unchanged_count=len(diff.unchanged),
            deleted_count=len(diff.deleted),
            skipped_count=len(diff.skipped),
            skipped_reasons=diff.skipped_reasons(),
            deletions_recorded=journal.intents_recorded,
            deletions_unresolved=progress.deletions_unresolved,
            publish_failed_count=len(progress.publish_failed),
            metadata_full_repush_count=progress.metadata_full_repush,
            source_url_fallback_count=progress.source_url_fallback,
            source_url_mismatch_count=progress.source_url_mismatch,
        )

    def _check_row(self, row: DocumentRef, seen: set[str]) -> None:
        """classify() refuses these for a whole row set; one row at a time
        they have to be refused here."""
        if row.document_id in seen:
            raise ValueError(f"document {row.document_id} appears twice in the rows")
        if row.agency_id != self.agency_id:
            raise ValueError(
                f"document {row.document_id} belongs to another agency than this run's"
            )
        seen.add(row.document_id)

    # -- one document (F5) -------------------------------------------------

    def _publish_one(
        self,
        row: DocumentRef,
        entry: ManifestDocumentEntry | None,
        progress: _Progress,
        journal: DeletionJournal,
    ) -> DocumentDiff | None:
        """Classify one row and publish it if its bucket asks for that. The
        text, chunks and record die with this call (E11)."""
        text: str | None = None
        sidecar: ParsedSidecar | None = None
        if needs_read(entry, row, chunker_fingerprint=self._fingerprint):
            read = self._source.read(row)
            _check_read(row, read)
            row, text, sidecar = read.ref, read.text, read.sidecar
            if row.skip_reason is not None:
                progress.read_skipped.add(row.document_id)

        change = classify_document(entry, row, chunker_fingerprint=self._fingerprint)
        if change is None or change.bucket not in _PUBLISH_BUCKETS:
            return change
        # Only a read row reaches a publish bucket: an unread one that Gate 2
        # spared is unchanged, so text and sidecar are both present here.
        if text is None or sidecar is None:
            raise ValueError(f"document {row.document_id} reached publish unread")
        if sidecar.source_url_origin == "row":
            progress.source_url_fallback += 1
        progress.source_url_mismatch += sidecar.source_url_mismatch

        if change.bucket is Bucket.METADATA_CHANGED:
            self._publish_metadata(row, entry, text, sidecar, progress)
            return change

        chunks = chunk_document(
            text,
            agency_id=self.agency_id,
            document_id=row.document_id,
            chunker=self._chunker,
        )
        if len(chunks) > self._max_chunks:
            # Poison-pill isolation: skipped, never raised. A document published
            # before is held, so its live chunks stay exactly as they are.
            logger.warning(
                "document %s: %d chunks exceeds MAX_CHUNKS_PER_DOCUMENT=%d; skipped",
                row.document_id,
                len(chunks),
                self._max_chunks,
            )
            progress.demoted[row.document_id] = TOO_MANY_CHUNKS
            return change
        progress.chunk_counts[row.document_id] = len(chunks)

        outcome = self._sink.upsert_document(
            _record(row, sidecar, len(chunks), progress), chunks
        )
        if self._note_upsert(outcome, progress) and entry is not None:
            # Only after the upsert landed: until then the record still says
            # the old count, and those ordinals are what it points at.
            self._delete_tail(
                row.document_id, entry.chunk_count, len(chunks), progress, journal
            )
        return change

    def _publish_metadata(
        self,
        row: DocumentRef,
        entry: ManifestDocumentEntry | None,
        text: str,
        sidecar: ParsedSidecar,
        progress: _Progress,
    ) -> None:
        """metadata_changed: the record only, or a full re-push where the
        sink cannot merge (L8). Asked of the declared capability rather than
        tried and caught, so the expensive path is a decision, not an
        accident."""
        # Narrowing only: DocumentDiff refuses metadata_changed with no entry.
        assert entry is not None
        if self._sink.capabilities.supports_metadata_only_merge:
            outcome = self._sink.upsert_document(
                _record(row, sidecar, entry.chunk_count, progress), None
            )
            self._note_upsert(outcome, progress)
            return

        chunks = chunk_document(
            text,
            agency_id=self.agency_id,
            document_id=row.document_id,
            chunker=self._chunker,
        )
        # Same content, same fingerprint: the chunker is deterministic, so the
        # count should be the published one. If it is not, a metadata-only
        # bucket cannot record the new count, and pushing these chunks would
        # leave the destination and the manifest disagreeing. So this
        # document is held, not the run aborted — one document must not wedge
        # the agency — and marked so the next run republishes it in full as
        # content_changed. The MAX_CHUNKS guard is not reapplied: these
        # chunks are already live.
        if len(chunks) != entry.chunk_count:
            logger.warning(
                "document %s: re-chunking unchanged content gave %d chunks, but "
                "%d are published; held, and republished in full next run",
                row.document_id,
                len(chunks),
                entry.chunk_count,
            )
            progress.publish_failed.add(row.document_id)
            progress.unconfirmed.add(row.document_id)
            return
        progress.metadata_full_repush += 1
        outcome = self._sink.upsert_document(
            _record(row, sidecar, len(chunks), progress), chunks
        )
        self._note_upsert(outcome, progress)

    def _note_upsert(self, outcome: UpsertOutcome, progress: _Progress) -> bool:
        if outcome.published:
            progress.published.add(outcome.document_id)
            return True
        progress.publish_failed.add(outcome.document_id)
        logger.warning(
            "document %s not published: %s (%s); its previous manifest entry "
            "is kept and it is retried next run",
            outcome.document_id,
            outcome.status.value,
            outcome.detail or "-",
        )
        return False

    def _delete_tail(
        self,
        document_id: str,
        previous_count: int,
        new_count: int,
        progress: _Progress,
        journal: DeletionJournal,
    ) -> None:
        """The ordinals a shrunk document no longer has, journalled first."""
        tail = tail_coordinates(document_id, previous_count, new_count)
        if not tail:
            return
        ordinals = [coordinate.ordinal for coordinate in tail]
        journal.intend_chunk_delete(document_id, ordinals)
        outcome = self._sink.delete_chunks(document_id, ordinals)
        journal.chunk_delete_done(document_id, outcome)
        if outcome.is_complete:
            progress.tails_deleted.add(document_id)
        else:
            progress.tails_unresolved.add(document_id)
            progress.deletions_unresolved += len(outcome.unresolved)

    # -- whole documents (F4, F7) ------------------------------------------

    def _delete_documents(self, progress: _Progress, journal: DeletionJournal) -> None:
        reasons = {
            change.document_id: change.reason or ""
            for change in progress.changes
            if change is not None and change.bucket is Bucket.DELETED
        }
        if not reasons:
            return
        # Journalled before the sink is asked. The manifest is about to forget
        # these documents, so from here on the journal is their only record.
        journal.intend_document_deletes(reasons)
        outcome = self._sink.delete_documents(list(reasons))
        journal.document_deletes_done(outcome)
        progress.deletions_unresolved += len(outcome.unresolved)
        if outcome.unresolved:
            logger.warning(
                "%d of %d document deletes unresolved; journalled for the drain",
                len(outcome.unresolved),
                len(reasons),
            )

    # -- the manifest ------------------------------------------------------

    def _assemble(self, previous: Manifest | None, progress: _Progress) -> DiffResult:
        diff = DiffResult.from_documents(
            progress.changes,
            first_run=previous is None,
            fingerprint_changed=(
                previous is not None
                and previous.chunker_fingerprint != self._fingerprint
            ),
            chunker_fingerprint=self._fingerprint,
        )
        for document_id, reason in progress.demoted.items():
            diff = diff.demote_to_skipped(document_id, reason)
        # The tails recomputed here come from the same tail_coordinates() the
        # loop deleted by, so the two cannot disagree; record() checks it.
        return diff.with_chunk_tails(progress.chunk_counts)

    def _reconcile_journals(
        self,
        run_id: str,
        to_close: Sequence[DeletionJournal],
        candidates: Sequence[DeletionJournal],
    ) -> None:
        """After the commit, so nothing here may fail the run. Close the
        journals this commit reconciled, then mirror every closed journal not
        yet mirrored at its current length — including one an earlier run
        failed to mirror. The local files are the journals of record; the
        mirror is a copy."""
        for journal in _unique(to_close):
            try:
                journal.close(closed_by=run_id)
            except OSError as exc:
                # Left open, the next run distrusts its documents again: a
                # needless republish, never a lost one.
                logger.warning(
                    "run %s: could not close the journal of run %s (%s: %s)",
                    run_id,
                    journal.run_id,
                    type(exc).__name__,
                    sanitize_sensitive_text(str(exc)),
                )
        for journal in _unique(candidates):
            if journal.needs_mirror():
                self._mirror_journal(run_id, journal)

    def _mirror_journal(self, run_id: str, journal: DeletionJournal) -> None:
        payload = journal.payload()
        if payload is None:
            return
        try:
            self._manifests.mirror_deletion_journal(journal.run_id, payload)
        except Exception as exc:
            # A copy must not fail a run that has already committed; the next
            # commit tries again.
            logger.warning(
                "run %s: the deletion journal of run %s was not mirrored "
                "(%s: %s); the local copy at %s is the record",
                run_id,
                journal.run_id,
                type(exc).__name__,
                sanitize_sensitive_text(str(exc)),
                journal.path,
            )
            return
        journal.mark_mirrored(payload)

    # -- reporting (A15) ---------------------------------------------------

    def _report(self, run_id: str, outcome: RunOutcome, started_at: str) -> RunReport:
        return RunReport(
            run_id=run_id,
            agency_id=self.agency_id,
            sink_id=self._sink.sink_id,
            outcome=outcome,
            started_at=started_at,
            finished_at=self._clock(),
        )

    def _observe(self, report: RunReport, duration_seconds: float) -> None:
        counts = {
            Bucket.NEW.value: report.new_count,
            Bucket.CONTENT_CHANGED.value: report.content_changed_count,
            Bucket.METADATA_CHANGED.value: report.metadata_changed_count,
            Bucket.UNCHANGED.value: report.unchanged_count,
            Bucket.DELETED.value: report.deleted_count,
            Bucket.SKIPPED.value: report.skipped_count,
        }
        # RunReport.outcome is a plain str; every report here was built by
        # _report(), which only takes a RunOutcome.
        outcome = cast(RunOutcome, report.outcome)
        log_run_outcome(
            outcome=outcome,
            run_id=report.run_id,
            agency_id=report.agency_id,
            sink=report.sink_id,
            duration_seconds=duration_seconds,
            counts=counts,
            deletions_recorded=report.deletions_recorded,
        )
        if self._run_state is not None:
            self._run_state.record(
                outcome=outcome,
                run_id=report.run_id,
                agency_id=report.agency_id,
                duration_seconds=duration_seconds,
            )


def _distrust(
    entries: Mapping[str, ManifestDocumentEntry],
    unreconciled: Sequence[DeletionJournal],
) -> Mapping[str, ManifestDocumentEntry]:
    """`entries`, with every entry an unreconciled journal names marked
    UNCONFIRMED_FINGERPRINT; see the module docstring. An entry the journal
    names that the manifest no longer lists was committed away already, and
    needs nothing."""
    attempted: set[str] = set()
    for journal in unreconciled:
        attempted |= journal.attempted_documents()
    suspect = entries.keys() & attempted
    if not suspect:
        return entries
    logger.warning(
        "%d published document(s) were touched by a run that never committed; "
        "they are republished in full, or deleted again",
        len(suspect),
    )
    return {
        document_id: (
            replace(entry, chunker_fingerprint=UNCONFIRMED_FINGERPRINT)
            if document_id in suspect
            else entry
        )
        for document_id, entry in entries.items()
    }


def _unique(journals: Sequence[DeletionJournal]) -> list[DeletionJournal]:
    """One journal per file, first wins."""
    seen: dict[Path, DeletionJournal] = {}
    for journal in journals:
        seen.setdefault(journal.path, journal)
    return list(seen.values())


def _record(
    row: DocumentRef, sidecar: ParsedSidecar, chunk_count: int, progress: _Progress
) -> DocumentRecord:
    return build_document_record(
        row, sidecar, chunk_count=chunk_count, synced_at=progress.synced_at
    )


def _check_read(row: DocumentRef, read: ReadDocument) -> None:
    """The source may add hashes or a skip reason to a row, and nothing else:
    a read that came back as another document, or with the row's own fields
    changed, would publish something the enumeration never described."""
    unread = replace(
        read.ref,
        raw_sha256=None,
        content_sha256=None,
        metadata_sha256=None,
        skip_reason=None,
    )
    if unread != replace(row, skip_reason=None):
        raise ValueError(
            f"document {row.document_id}: the source returned a read that does "
            "not match the row it was asked for"
        )
