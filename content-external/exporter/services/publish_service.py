"""The publish coordinator — the one stateful component (Stage F, F2–F21).
Key Designs #5 and #7.

ORDERING IS THE DELIVERABLE, AND MOST OF IT IS NOT HERE. "No chunk is
retrievable while its document's record is missing" (F3) and "on delete,
content goes before the record" (F4) are guarantees of the ContentSink
contract. The object-store sink meets them by write order and the llm-module
sink by one request per document. The sink conformance suite (D15) asserts
them per implementation. This module calls `upsert_document(record, chunks)`
and `delete_documents(ids)` and never sequences a destination's writes itself,
which is why it holds a ContentSink and a manifest service — never a store
client, never a transport — and never asks which sink it holds (A13).

WHAT THIS MODULE DOES ORDER, across documents and across the run:

    take the run lock (F2)  — held: record `busy`, touch nothing else
    read the corpus watermark, then load the previous manifest (F6, F17)
    Gate 1 (E4): nothing moved and no journal pending -> `unchanged`, done
    distrust every entry an uncommitted run's journal touched (F7)
    enumerate the rows
    on a first run only, refuse a destination that already holds content
        (F21), then mark the bootstrap in progress
    per row, streamed (E11), up to EXPORT_CONCURRENCY at once (F16):
        prepare, on a worker thread:
            read it only if the diff says it must (Gate 2)
            classify it, then dispatch by bucket (F5):
                new, content_changed  -> chunk, upsert_document(record, chunks)
                metadata_changed      -> upsert_document(record, None),
                                         or a full re-push where the sink
                                         cannot merge (L8), counted
                unchanged, skipped    -> nothing
                deleted               -> collected
        apply, on the coordinator thread, in row order:
            account for the outcome (F18), count permanent failures (F19)
            journal + delete_chunks(tail) once the upsert landed
            checkpoint, on a first run (F15)
    journal, then delete_documents(every deleted id) (F4, F7)
    build the next manifest with diff.record(), then COMMIT it (F6)
    close the journals it reconciled, and mirror the unmirrored ones (F7)
    update the dead-letter record (E9)

THE COMMIT IS THE ONLY POINT OF NO RETURN, and any exception before it fails
the run with nothing committed; the next tick is the retry. An upsert before it
is replayable — chunk ids are coordinates, so a replay overwrites rather than
adds — provided the row still moved. A delete is not replayable on its own: a
run that deleted a document (or a tail) and then died leaves a manifest that
still describes content the destination no longer holds, and if the row is
restored with the same content the diff would call it unchanged. So every
entry named in a journal no committed run has closed is distrusted before
classification: its fingerprint becomes UNCONFIRMED_FINGERPRINT, which reads
and republishes it in full, or deletes it again if its row is still deleted.

A document the destination did not confirm is not an exception: it keeps its
previous manifest entry, is counted and named in the report with its status
(F18), and is retried next run, and the corpus watermark is withheld so the
next run cannot skip it. Neither is a document whose re-chunk disagrees with
its published count: it is held, marked UNCONFIRMED_FINGERPRINT, and
republished in full next run.

CONCURRENCY (F16) is across documents, never within one, so F3's
per-document guarantee is untouched: a document's record, chunks and tail
delete are still issued in order, by one thread each. Workers only read,
chunk and upsert; everything that changes the run's state — the progress
counts, the deletion journal, the checkpoints — happens on the coordinator
thread, applying results in row order. That keeps the manifest single-writer
and makes a concurrent run commit the same bytes as a sequential one. At most
`concurrency` documents hold text at once, which is the bound A16's memory
budget multiplies by; more rows may be queued or finished ahead of the oldest
unapplied one, but those carry no text. The effective value is
min(EXPORT_CONCURRENCY, the sink's declared max_concurrency): the
destination's throughput is the ceiling, not a number chosen here.

THE SYSTEMIC-FAILURE ABORT (F19). A per-document permanent failure is
isolated: counted, held, retried next tick. But SINK_FAILURE_ABORT_THRESHOLD
consecutive permanent failures, counted in row order, mean the destination is
what is broken — an expired credential, a wrong base URL — and the run aborts
with nothing committed rather than producing thousands of failed pushes and a
retry storm on someone else's service. A published document resets the
count; a retryable or deferred one neither counts nor resets it. Because
results are counted in row order while workers run ahead, up to one window
of rows past the tripping one may already have been attempted; from the trip
on, no worker starts another upsert.

BOOTSTRAP CHECKPOINTS (F15), on a first run only. With no previous manifest
the deletion set is empty by construction and only published documents enter
the manifest, so a checkpoint under-claims and can never over-claim. It is
committed every BOOTSTRAP_CHECKPOINT_EVERY published documents with the
corpus watermark withheld, so a killed bootstrap resumes: the next run walks
every row and Gate 2 spares what the checkpoint recorded, at zero object
reads. On an incremental run a partial commit could drop carried-forward
entries, so `_maybe_checkpoint` refuses anything but a first run, and that is
the one place the rule lives.

A FIRST RUN REFUSES A POPULATED DESTINATION (F21). DIFF_ON_MISSING_MANIFEST=
first_run is meant for a deployment's bootstrap. Later, a missing manifest
means something deleted it, and a cold start would republish the corpus and
permanently orphan every document CKB deleted while it was gone. So before
accepting a first run the destination is probed — a listing on a listable
sink, read-backs of the first enumerated rows otherwise — and a populated one
fails the run. A bootstrap of our own that died before its first checkpoint
is recognised by the marker in services/bootstrap_marker.py and resumes. The
marker exists only while no manifest does: every run that finds a manifest
removes it first, and fails if it cannot. An operator who means it passes
--accept-existing-content, from the CLI only; such a run never writes a
marker, because the content it publishes into is not all this service's
own. A sink that can neither list nor read back cannot be probed; the run
proceeds, reports `unverifiable`, and the config flip remains the only
defence.

A LIMITED RUN (F10's --limit) caps the rows that need a read, never the
enumeration: a row left out of the rows would classify `deleted` as absent.
Rows past the cap are skipped with LIMIT_REACHED and carried verbatim.

"I SAW THE WHOLE CORPUS" IS ONE FLAG (F20). `_Progress.observed_full_corpus`
is false after --limit, an F19 abort or a run that ended early, and it is
defined in exactly one place. It withholds the corpus watermark — without
that, `--limit 25` on an incremental run would record the full watermark and
the next tick would report `unchanged` while every other changed document
stayed unpublished — and it is reported as `run_observed_full_corpus`, which
every sweep must check through services/corpus_guard.py. Seeing the whole
corpus is necessary for a sweep but not sufficient: a document the run held
back (`documents_held_back`: not published, tail unconfirmed, skipped by a
guard) keeps its old state and its old synced_at, and a sweep would take it
for stale. The guard refuses those runs too.

GATE 1 (E4) ends a run as `unchanged` before the enumeration when
core/gate.can_skip_run() matches the committed manifest and `force` is
absent, so a quiet tick costs one watermark query and one manifest read. Two
things it cannot see keep it from skipping. A journal no committed run has
closed: only a run that walks the rows reconciles it, and skipping would
leave its entries distrusted for as long as CKB stays quiet. And a closed
journal still waiting for its mirror, retried only after a commit. An
`unchanged` run classified nothing, so it reports
run_observed_full_corpus=False and drives no sweep.

THE STALE SWEEP (L10) runs after the commit, under the lock, when
STALE_SWEEP_ENABLED is set and corpus_guard.require_observed_full_corpus
passes. It sends the committed manifest's documents as a keep list, because
an unchanged document is never re-sent and keeps an old synced_at while
being live. It never changes the outcome. A sink without one reports
`not_supported`; Stage J's ordinal tail sweep (J5) is the listable sinks'.

A PENDING RE-CHUNK (L18) on a sink that declares rechunk_requires_ack fails
the run before Gate 1, having sent nothing, unless the CLI passed
--accept-rechunk: on the llm-module sink a fingerprint change is a full
corpus re-embed, and it must be an announced, operator-driven run rather than
a config bump that lands at :17.

Backoff (F8) is the transport's, never this module's: a document the
destination did not confirm is retried next tick.

REDACTION (F13). Log lines carry ids, counts and sanitised reason codes. Never
document text, chunk text, a URL or a request/response body, and an exception
is logged by type and sanitised message — never with a traceback, whose frames
could hold a document.
"""

import logging
import threading
import time
import unicodedata
from collections import Counter, deque
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime
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
from exporter.core.gate import can_skip_run
from exporter.core.metadata import build_document_record
from exporter.core.redaction import sanitize_sensitive_text
from exporter.core.schemas import (
    AgencyWatermark,
    Chunk,
    DocumentRecord,
    DocumentRef,
    Manifest,
    ManifestDocumentEntry,
    ParsedSidecar,
    ReadDocument,
    RunReport,
)
from exporter.services.bootstrap_marker import BootstrapMarker
from exporter.services.corpus_guard import (
    IncompleteCorpusError,
    require_observed_full_corpus,
)
from exporter.services.dead_letter import DeadLetterStore
from exporter.services.deletion_journal import DeletionJournal, agency_journals
from exporter.services.document_source import DocumentSource
from exporter.services.locking import run_lock
from exporter.services.manifest_service import ManifestService
from exporter.services.run_log import log_run_outcome
from exporter.services.run_state import (
    RECHUNK_REFUSED,
    RunOutcome,
    RunStateStore,
    utc_now_iso,
)
from exporter.sinks.base import (
    ContentSink,
    NotSupported,
    UpsertOutcome,
    UpsertStatus,
)
from exporter.stores.base import StoreError

__all__ = [
    "LIMIT_REACHED",
    "READ_BACK_PROBE_LIMIT",
    "RECHUNK_COUNT_MISMATCH",
    "TOO_MANY_CHUNKS",
    "UNCONFIRMED_FINGERPRINT",
    "EmptyEnumerationError",
    "NonEmptyDestination",
    "PublishCoordinator",
    "RechunkNotAcknowledged",
    "SystemicSinkFailure",
]

logger = logging.getLogger(__name__)

# The skip reason for a document over MAX_CHUNKS_PER_DOCUMENT, a guard that
# can only run after chunking (E9; DiffResult.demote_to_skipped).
TOO_MANY_CHUNKS = "too_many_chunks"

# The skip reason for a row a --limit run did not get to (F10). Here and not
# in core/diff.py's SkipReason: it is a property of how the run was invoked,
# not of the row.
LIMIT_REACHED = "limit_reached"

# F18's reason for a document held because re-chunking its unchanged content
# disagreed with the published count: not a sink outcome, so not an
# UpsertStatus, but a document that did not publish all the same.
RECHUNK_COUNT_MISMATCH = "rechunk_count_mismatch"

# F21 on a sink that cannot list: how many enumerated rows are read back to
# decide whether the destination already holds the agency. A probe, not a
# reconcile — one hit refuses the run, and a populated destination holds most
# of the rows, so a short prefix of them answers.
READ_BACK_PROBE_LIMIT = 25

# F16: rows submitted ahead of the oldest unapplied one, per worker. See
# _publish_rows for why the window is wider than the pool.
_WINDOW_PER_WORKER = 4

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


class SystemicSinkFailure(StoreError):  # noqa: N818
    """SINK_FAILURE_ABORT_THRESHOLD consecutive permanent upsert failures
    (F19): the destination is broken, not a document. Permanent, because the
    next tick meets the same destination."""

    def __init__(self, message: str) -> None:
        super().__init__(message, permanent=True)


class NonEmptyDestination(StoreError):  # noqa: N818
    """A first run found the destination already holding the agency's
    content (F21). Never a cold start: see the module docstring."""

    def __init__(self, message: str) -> None:
        super().__init__(message, permanent=True)


class RechunkNotAcknowledged(StoreError):  # noqa: N818
    """A re-chunk is pending on a destination that re-embeds what it
    receives, and the operator did not pass --accept-rechunk (L18).
    Permanent: every tick meets the same fingerprint until someone runs it
    by hand."""

    def __init__(self, message: str) -> None:
        super().__init__(message, permanent=True)


class _RunStopped(Exception):  # noqa: N818
    """A worker gave up because the run is already failing. Never reported:
    the failure that set the stop is the run's outcome, and this future's
    result is never read."""


@dataclass(frozen=True)
class _DocumentWork:
    """What preparing one row produced, for the coordinator thread to apply.
    Carries no text and no chunks: they die on the worker (E11)."""

    document_id: str
    change: DocumentDiff | None
    # Skipped by a read-time guard (oversize, a rejected sidecar).
    read_skipped: bool = False
    source_url_fallback: bool = False
    source_url_mismatch: bool = False
    # The MAX_CHUNKS_PER_DOCUMENT skip reason, when the guard fired.
    demoted: str | None = None
    # The chunk count this run cut, for new and content_changed.
    chunk_count: int | None = None
    # A metadata_changed document re-pushed in full (L8).
    metadata_full_repush: bool = False
    # Held: its re-chunk disagreed with the published count.
    unconfirmed: bool = False
    outcome: UpsertOutcome | None = None
    # (previous chunk_count, new chunk_count): the tail to delete once the
    # upsert has landed, for a document published before.
    tail: tuple[int, int] | None = None


@dataclass
class _Progress:
    """What the per-row loop learned, for the diff, the manifest and the
    report. Mutable, private to one run, and only ever touched by the
    coordinator thread."""

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
    # Rows with a deletion signal that were never published: classify_document
    # puts them in no bucket, but for E9's record they are resolved.
    ended_unpublished: set[str] = field(default_factory=set)
    # Held because their published chunks could not be vouched for; marked
    # UNCONFIRMED_FINGERPRINT in the next manifest.
    unconfirmed: set[str] = field(default_factory=set)
    deletions_unresolved: int = 0
    metadata_full_repush: int = 0
    source_url_fallback: int = 0
    source_url_mismatch: int = 0
    # F10: rows that needed a read and got one under --limit, and whether
    # any row was skipped for want of the cap.
    reads_under_limit: int = 0
    limited: bool = False
    # F15: first-run checkpoints committed, and the published count at the
    # last one.
    checkpoints_committed: int = 0
    published_at_checkpoint: int = 0
    # F18: every upsert's status, counted, and why each unpublished document
    # did not publish.
    upsert_outcomes: Counter[str] = field(default_factory=Counter)
    publish_failures: dict[str, str] = field(default_factory=dict)
    # F19: permanent failures since the last published document.
    consecutive_permanent: int = 0
    # F20: every row was classified and applied, and no abort fired.
    loop_completed: bool = False
    aborted: bool = False
    # F21, first run only.
    first_run_probe: str | None = None
    # L18: None when no re-chunk was pending.
    rechunk: str | None = None

    @property
    def observed_full_corpus(self) -> bool:
        """F20. The only definition: the whole row set was classified, no
        --limit held any of it back, and no abort cut the run short.

        "--limit held any of it back" is deliberate, and narrower than "--limit
        was passed": a cap larger than the reads the run needed skipped
        nothing, so that run did classify the whole corpus."""
        return self.loop_completed and not self.limited and not self.aborted

    @property
    def held_back(self) -> set[str]:
        """Documents this run should have brought up to date and did not: not
        published, a tail not confirmed deleted, or skipped by a guard whose
        verdict depends on configuration. Each keeps its previous state and
        its old synced_at, so it withholds the watermark and makes any sweep
        unsafe, however completely the rows were observed."""
        return (
            self.publish_failed
            | self.tails_unresolved
            | self.demoted.keys()
            | self.read_skipped
        )


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
        bootstrap_checkpoint_every: int,
        concurrency: int,
        sink_failure_abort_threshold: int,
        run_state: RunStateStore | None = None,
        clock: Callable[[], str] = utc_now_iso,
        stale_sweep_enabled: bool = False,
    ) -> None:
        if manifests.sink_id != sink.sink_id:
            raise ValueError(
                "the manifest service and the sink name different destinations"
            )
        if max_chunks_per_document < 1:
            raise ValueError("max_chunks_per_document must be >= 1")
        if bootstrap_checkpoint_every < 1:
            raise ValueError("bootstrap_checkpoint_every must be >= 1")
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        if sink_failure_abort_threshold < 1:
            raise ValueError("sink_failure_abort_threshold must be >= 1")
        self._sink = sink
        self._manifests = manifests
        self._source = source
        self._chunker = chunker
        self._fingerprint = chunker_fingerprint
        self._work_dir = work_dir
        self._max_chunks = max_chunks_per_document
        self._checkpoint_every = bootstrap_checkpoint_every
        self._requested_concurrency = concurrency
        # F16: the destination's ceiling wins over the configured value.
        self._concurrency = min(concurrency, sink.capabilities.max_concurrency)
        self._abort_threshold = sink_failure_abort_threshold
        self._run_state = run_state
        self._clock = clock
        self._stale_sweep_enabled = stale_sweep_enabled

    @property
    def agency_id(self) -> str:
        return self._sink.agency_id

    @property
    def concurrency(self) -> int:
        """The documents in flight at once: min(EXPORT_CONCURRENCY, the
        sink's max_concurrency)."""
        return self._concurrency

    def export(
        self,
        run_id: str,
        *,
        force: bool = False,
        limit: int | None = None,
        accept_existing_content: bool = False,
        accept_rechunk: bool = False,
    ) -> RunReport:
        """Run one export and return its report. An Exception, including a
        lock file that cannot be opened, is the report's `failed` outcome
        rather than a raise, and the next tick is the retry. Only a
        BaseException (SystemExit, KeyboardInterrupt) escapes.

        `force` bypasses Gate 1 (E4), so the run walks every row even when
        nothing moved; it is echoed in the report. `limit` caps the
        documents read this run (F10). `accept_existing_content` lets a first
        run publish into a destination that already holds this agency's
        content (F21), and `accept_rechunk` lets a pending re-chunk run on a
        destination that re-embeds what it receives (L18) — each an
        operator's deliberate act, which only the CLI offers."""
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
        ):
            raise ValueError(f"limit must be an int >= 1, got {limit!r}")
        started_at = self._clock()
        started = time.perf_counter()
        asked = {
            "forced": force,
            "limit": limit,
            "accepted_existing_content": accept_existing_content,
            "accepted_rechunk": accept_rechunk,
        }
        report: RunReport | None = None
        try:
            with run_lock(self._work_dir) as acquired:
                if acquired:
                    report = self._attempt(
                        run_id,
                        started_at,
                        force=force,
                        limit=limit,
                        accept_existing_content=accept_existing_content,
                        accept_rechunk=accept_rechunk,
                    )
                    # Recorded while the lock is still held: once it is free
                    # the next run may start, and its record must not race
                    # this one's. A `busy` can still interleave; it is only
                    # a counter.
                    report = self._finish(report, started, asked)
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
            report = self._report(
                run_id, "failed", started_at, error_type=type(exc).__name__
            )
        else:
            # Reached only when the lock was not acquired: the acquired branch
            # returns from inside it.
            logger.info("run %s: another export or drain holds the lock", run_id)
            report = self._report(run_id, "busy", started_at)
        return self._finish(report, started, asked)

    def _attempt(
        self,
        run_id: str,
        started_at: str,
        *,
        force: bool,
        limit: int | None,
        accept_existing_content: bool,
        accept_rechunk: bool,
    ) -> RunReport:
        # Built here rather than in _run so a failure can still report the
        # checkpoints it committed, which the next run resumes from, and the
        # outcomes it saw, which are why an F19 abort fired.
        progress = _Progress(synced_at=started_at)
        try:
            return self._run(
                run_id,
                started_at,
                progress,
                force=force,
                limit=limit,
                accept_existing_content=accept_existing_content,
                accept_rechunk=accept_rechunk,
            )
        except Exception as exc:
            # Every failure is `failed`, and nothing was committed beyond the
            # first-run checkpoints counted below. Type and sanitised message
            # only, never a traceback; see the module docstring.
            logger.error(
                "run %s failed, nothing committed: %s: %s",
                run_id,
                type(exc).__name__,
                sanitize_sensitive_text(str(exc)),
            )
            # run_observed_full_corpus stays False: a failed run committed
            # nothing that could be concluded from, whatever it had walked.
            return replace(
                self._report(
                    run_id, "failed", started_at, error_type=type(exc).__name__
                ),
                **_outcome_fields(progress),
            )

    def _finish(
        self, report: RunReport, started: float, asked: Mapping[str, object]
    ) -> RunReport:
        """Stamp how the run was asked for and how long it took, then record
        it (A15). Every outcome passes through here exactly once."""
        report = replace(
            report, duration_seconds=time.perf_counter() - started, **asked
        )
        if self._stale_sweep_enabled and report.stale_sweep == "disabled":
            # Only a committed run reaches the sweep step, and that step
            # always names what it did. Anything still at the default is an
            # `unchanged`, `failed` or `busy` run with the sweep switched ON,
            # and "disabled" would tell the operator it is off.
            report = replace(report, stale_sweep="not_run")
        if report.outcome in ("failed", "busy"):
            # Neither updates the record, but it still stands: report its
            # size, so an alert on `dead_lettered=` does not see it drop to 0
            # on every failed or busy tick.
            report = replace(report, dead_lettered_count=self._dead_letter_size())
        try:
            self._observe(report)
        except Exception as exc:
            # Observability only. The run is over and, on success, committed;
            # failing to log or record it must not turn it into a raise.
            logger.warning(
                "run %s: its outcome could not be recorded: %s",
                report.run_id,
                type(exc).__name__,
            )
        return report

    # -- the run -----------------------------------------------------------

    def _run(
        self,
        run_id: str,
        started_at: str,
        progress: _Progress,
        *,
        force: bool,
        limit: int | None,
        accept_existing_content: bool,
        accept_rechunk: bool,
    ) -> RunReport:
        agency_id = self.agency_id
        journal = DeletionJournal(self._work_dir, run_id=run_id, agency_id=agency_id)
        if journal.is_closed():
            # Appending to it would merge two runs' records under one id, and
            # the mirror would publish them as one.
            raise ValueError(
                f"run id {run_id} already committed for this agency; a new run "
                "needs a new id"
            )
        marker = BootstrapMarker(
            self._work_dir, agency_id=agency_id, sink_id=self._sink.sink_id
        )

        # The watermark before the rows: a row that moves in between makes the
        # recorded watermark older than what was read, which only costs the
        # next run a walk. The other order could claim a watermark for a row
        # this run never saw.
        watermark = self._source.watermark(agency_id)
        previous = self._manifests.load_previous(agency_id)
        if previous is not None:
            # F21. The marker may exist only while no manifest does. One left
            # behind — a commit whose clear failed, a crash between commit and
            # clear — would otherwise outlive the manifest and, should it ever
            # be lost, vouch for content and wave a cold start through the
            # probe. Cleared on every incremental run, and fail-closed: a
            # marker that cannot be removed fails the run, loudly, rather than
            # surviving silently.
            marker.clear()
            self._check_rechunk(previous, progress, accept_rechunk=accept_rechunk)
        on_disk = agency_journals(self._work_dir, agency_id)
        unreconciled = [j for j in on_disk if not j.is_closed()]
        if (
            not unreconciled
            and not any(j.needs_mirror() for j in on_disk)
            and can_skip_run(previous, watermark, self._fingerprint, force=force)
        ):
            # Gate 1 (E4): nothing read, nothing written; see the module
            # docstring for why a pending journal keeps the run walking.
            logger.info(
                "run %s: agency %s unchanged since the committed manifest",
                run_id,
                agency_id,
            )
            return replace(
                self._report(run_id, "unchanged", started_at),
                dead_lettered_count=self._dead_letter_size(),
                **_outcome_fields(progress),
            )
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
        if previous is None:
            self._check_first_run(
                run_id,
                rows,
                marker,
                progress,
                accept_existing_content=accept_existing_content,
            )

        # Every row is classified, and its document published, before any
        # whole document is deleted.
        seen = self._publish_rows(
            run_id,
            rows,
            entries,
            previous=previous,
            watermark=watermark,
            progress=progress,
            journal=journal,
            marker=marker,
            limit=limit,
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

        diff, manifest = self._next_manifest(
            previous,
            progress,
            watermark,
            # F20 first: a run that saw a slice cannot vouch for the corpus.
            # Then the documents it should have published and did not, which
            # the next run must not skip either.
            advance_watermark=progress.observed_full_corpus and not progress.held_back,
        )
        self._commit(manifest, marker)  # F6: the commit point.
        self._reconcile_journals(run_id, [*unreconciled, journal], [*on_disk, journal])
        dead_lettered = self._record_dead_letters(run_id, diff, progress)

        report = replace(
            self._report(run_id, "success", started_at),
            dead_lettered_count=dead_lettered,
            new_count=len(diff.new),
            content_changed_count=len(diff.content_changed),
            metadata_changed_count=len(diff.metadata_changed),
            unchanged_count=len(diff.unchanged),
            deleted_count=len(diff.deleted),
            skipped_count=len(diff.skipped),
            skipped_reasons=diff.skipped_reasons(),
            deletions_recorded=journal.intents_recorded,
            deletions_unresolved=progress.deletions_unresolved,
            metadata_full_repush_count=progress.metadata_full_repush,
            source_url_fallback_count=progress.source_url_fallback,
            source_url_mismatch_count=progress.source_url_mismatch,
            run_observed_full_corpus=progress.observed_full_corpus,
            documents_held_back=len(progress.held_back),
            **_outcome_fields(progress),
        )
        return self._sweep_stale(report, manifest, progress)

    # -- after the commit: the stale sweep (L10) ---------------------------

    def _sweep_stale(
        self, report: RunReport, manifest: Manifest, progress: _Progress
    ) -> RunReport:
        """Delete what the destination holds for this agency that no
        committed document accounts for: synced before this run started, and
        absent from the manifest just committed.

        After the commit and still under the run lock, so nothing can
        publish in between. It never changes the outcome: the commit stands
        whatever the sweep does, and the next full run sweeps again. Off
        unless STALE_SWEEP_ENABLED, and refused unless the run vouches for
        the whole corpus (F20) — the one gate every sweep shares. A sink
        with no such sweep raises NotSupported, reported rather than
        branched on (A13): a listable sink sweeps by the ordinal tail rule
        instead (J5).
        """
        if not self._stale_sweep_enabled:
            return report
        try:
            require_observed_full_corpus(report, operation="the stale sweep")
        except IncompleteCorpusError as exc:
            logger.info("run %s: %s", report.run_id, exc)
            return replace(report, stale_sweep="refused")
        if not manifest.documents:
            # Keeping nothing is a purge (J7), never a sweep's side effect.
            logger.info(
                "run %s: stale sweep skipped, the manifest lists no document",
                report.run_id,
            )
            return replace(report, stale_sweep="refused")
        try:
            outcome = self._sink.sweep_stale(
                self.agency_id,
                datetime.fromisoformat(progress.synced_at),
                manifest.documents.keys(),
            )
        except NotSupported:
            return replace(report, stale_sweep="not_supported")
        except Exception as exc:
            logger.warning(
                "run %s: stale sweep failed, the commit stands: %s: %s",
                report.run_id,
                type(exc).__name__,
                sanitize_sensitive_text(str(exc)),
            )
            return replace(report, stale_sweep="failed")
        logger.info(
            "run %s: stale sweep removed %d document(s)",
            report.run_id,
            len(outcome.deleted),
        )
        return replace(
            report, stale_sweep="done", stale_swept_count=len(outcome.deleted)
        )

    # -- before Gate 1: a pending re-chunk (L18) ---------------------------

    def _check_rechunk(
        self, previous: Manifest, progress: _Progress, *, accept_rechunk: bool
    ) -> None:
        """Refuse a pending re-chunk on a destination that re-embeds every
        chunk it receives, unless the operator acknowledged it.

        Pending means the committed manifest, or any entry in it, was cut
        with another geometry than this run's — so an acknowledged `--limit`
        slice that re-chunked a few documents does not let the following
        cron ticks re-embed the rest unannounced. UNCONFIRMED_FINGERPRINT is
        not a geometry, only the coordinator's own distrust, and never
        counts. The check reads a capability, never a sink name (A13).
        """
        stale = {
            entry.chunker_fingerprint
            for entry in previous.documents.values()
            if entry.chunker_fingerprint != UNCONFIRMED_FINGERPRINT
        } | {previous.chunker_fingerprint}
        if stale <= {self._fingerprint}:
            return
        if not self._sink.capabilities.rechunk_requires_ack:
            progress.rechunk = "proceeded"
            return
        if accept_rechunk:
            logger.warning(
                "agency %s: re-chunking against a destination that re-embeds "
                "every chunk it receives, acknowledged by --accept-rechunk",
                self.agency_id,
            )
            progress.rechunk = "accepted"
            return
        progress.rechunk = RECHUNK_REFUSED
        raise RechunkNotAcknowledged(
            f"agency {self.agency_id}: the chunker fingerprint changed, and "
            f"sink {self._sink.sink_id!r} re-embeds every chunk it receives, so "
            "this is a full corpus re-embed on someone else's service. Run it "
            "by hand, announced, with --accept-rechunk (RUNBOOK: changing chunk "
            "geometry on the llm-module sink). Nothing was sent."
        )

    def _publish_rows(
        self,
        run_id: str,
        rows: Sequence[DocumentRef],
        entries: Mapping[str, ManifestDocumentEntry],
        *,
        previous: Manifest | None,
        watermark: AgencyWatermark,
        progress: _Progress,
        journal: DeletionJournal,
        marker: BootstrapMarker,
        limit: int | None,
    ) -> set[str]:
        """F16. Prepare up to `concurrency` rows at once on worker threads,
        and apply their results here, oldest first, so the run's state
        changes in row order whatever order the workers finish in. Returns
        the ids of the rows seen.

        The window of submitted rows is _WINDOW_PER_WORKER times the worker
        count, not equal to it. Results are applied in order, so with a
        window of exactly `concurrency` one slow document at the head would
        hold every finished slot behind it and idle the other workers. The
        wider window costs nothing in memory: a queued row is only its
        enumeration fields, and a finished one is a _DocumentWork with no
        text. Document text exists only on the `concurrency` running workers,
        which is the bound A16's budget multiplies by."""
        if self._concurrency < self._requested_concurrency:
            logger.info(
                "run %s: EXPORT_CONCURRENCY=%d capped at %d by the sink",
                run_id,
                self._requested_concurrency,
                self._concurrency,
            )
        seen: set[str] = set()
        window: deque[Future[_DocumentWork]] = deque()
        window_size = self._concurrency * _WINDOW_PER_WORKER
        stop = threading.Event()
        pool = ThreadPoolExecutor(
            max_workers=self._concurrency, thread_name_prefix="content-export"
        )

        def settle() -> None:
            self._apply(window.popleft().result(), progress, journal)
            self._maybe_checkpoint(previous, progress, watermark, marker)

        try:
            for row in rows:
                # Here, not on a worker: the duplicate check and --limit's
                # count must see the rows in order.
                self._check_row(row, seen)
                entry = entries.get(row.document_id)
                row = self._apply_limit(row, entry, progress, limit)
                window.append(
                    pool.submit(self._prepare, row, entry, progress.synced_at, stop)
                )
                if len(window) >= window_size:
                    settle()
            while window:
                settle()
        except BaseException:
            # Nothing new starts: queued rows are cancelled, and a worker
            # still reading or chunking sees `stop` and gives up before its
            # upsert. One already mid-upsert finishes below; its upsert is
            # replayable and nothing commits, so the next tick redoes it.
            stop.set()
            for future in window:
                future.cancel()
            raise
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        progress.loop_completed = True
        return seen

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

    def _apply_limit(
        self,
        row: DocumentRef,
        entry: ManifestDocumentEntry | None,
        progress: _Progress,
        limit: int | None,
    ) -> DocumentRef:
        """F10. The row, or the row skipped with LIMIT_REACHED once `limit`
        reads have been spent. Only a row that needs a read counts or is
        skipped: one Gate 2 spares, and a deletion signal, cost no read and
        are handled as on any run. A skipped row is carried verbatim by
        record() (C15), never deleted."""
        if limit is None or not needs_read(
            entry, row, chunker_fingerprint=self._fingerprint
        ):
            return row
        if progress.reads_under_limit < limit:
            progress.reads_under_limit += 1
            return row
        progress.limited = True
        return replace(row, skip_reason=LIMIT_REACHED)

    # -- the first run (F21) -----------------------------------------------

    def _check_first_run(
        self,
        run_id: str,
        rows: Sequence[DocumentRef],
        marker: BootstrapMarker,
        progress: _Progress,
        *,
        accept_existing_content: bool,
    ) -> None:
        """Probe the destination before a first run publishes anything,
        recording how it came out in `progress.first_run_probe` — "populated"
        included, set before NonEmptyDestination is raised, so the failed
        run's report says why. Then mark the bootstrap in progress, unless
        the operator overrode the probe."""
        probe = self._probe_first_run(rows, marker, accept_existing_content)
        progress.first_run_probe = probe
        if probe == "populated":
            raise NonEmptyDestination(
                f"agency {self.agency_id}: no manifest, but the destination "
                "already holds this agency's content. A first run here would "
                "republish the corpus and permanently orphan every document "
                "deleted while the manifest was gone. Restore the manifest, set "
                "DIFF_ON_MISSING_MANIFEST=fail, or, if this content is known to "
                "be safe to overwrite, run the CLI with --accept-existing-content."
            )
        if probe == "overridden":
            # The marker says "the content there is this service's own", and
            # after an override it is not: a run that then failed would leave
            # a marker vouching for foreign content on every later first run.
            # So an override never marks, and retires any marker it finds; a
            # crashed override needs the override again.
            marker.clear()
            return
        # Before the first upsert: from here until the first commit this
        # service has published content that no manifest records.
        marker.create(run_id)

    def _probe_first_run(
        self,
        rows: Sequence[DocumentRef],
        marker: BootstrapMarker,
        accept_existing_content: bool,
    ) -> str:
        """ "overridden", "resumed", "unverifiable", "empty" or "populated"."""
        if accept_existing_content:
            logger.warning(
                "first run for agency %s with --accept-existing-content: the "
                "destination is not checked, and whatever it already holds is "
                "overwritten where this run publishes and kept where it does not",
                self.agency_id,
            )
            return "overridden"
        found = marker.read()
        if found is not None:
            logger.info(
                "first run for agency %s resumes a bootstrap that never "
                "committed (marked by run %s at %s); its content in the "
                "destination is this service's own",
                self.agency_id,
                found.get("run_id", "-"),
                found.get("created_at", "-"),
            )
            return "resumed"

        capabilities = self._sink.capabilities
        if capabilities.can_list:
            populated = self._sink.holds_agency_content()
        elif capabilities.can_read_back:
            # A sample, not a reconcile: only the first READ_BACK_PROBE_LIMIT
            # rows are read back. Unreachable with ObjectStoreSink, whose
            # read-back is built on its listing; it is the llm-module's path.
            populated = any(
                self._sink.read_document(row.document_id) is not None
                for row in rows[:READ_BACK_PROBE_LIMIT]
            )
        else:
            logger.warning(
                "first run for agency %s: the sink can neither list nor read "
                "back, so whether it already holds content is unverifiable. "
                "DIFF_ON_MISSING_MANIFEST=fail is the only defence here once "
                "the bootstrap is done.",
                self.agency_id,
            )
            return "unverifiable"
        return "populated" if populated else "empty"

    # -- one document, on a worker (F5, F16) ---------------------------------

    def _prepare(
        self,
        row: DocumentRef,
        entry: ManifestDocumentEntry | None,
        synced_at: str,
        stop: threading.Event,
    ) -> _DocumentWork:
        """Classify one row and publish it if its bucket asks for that. Runs
        on a worker thread, so it touches no run state: what it learned is
        returned, and the text, chunks and record die with this call (E11).
        Gives up, before reading and again before upserting, once `stop` is
        set: the run is failing and nothing this worker does will be applied."""
        if stop.is_set():
            raise _RunStopped(row.document_id)
        text: str | None = None
        sidecar: ParsedSidecar | None = None
        read_skipped = False
        if needs_read(entry, row, chunker_fingerprint=self._fingerprint):
            read = self._source.read(row)
            _check_read(row, read)
            row, text, sidecar = read.ref, read.text, read.sidecar
            read_skipped = row.skip_reason is not None

        change = classify_document(entry, row, chunker_fingerprint=self._fingerprint)
        work = _DocumentWork(row.document_id, change, read_skipped=read_skipped)
        if change is None or change.bucket not in _PUBLISH_BUCKETS:
            return work
        # Only a read row reaches a publish bucket: an unread one that Gate 2
        # spared is unchanged, so text and sidecar are both present here.
        if text is None or sidecar is None:
            raise ValueError(f"document {row.document_id} reached publish unread")
        work = replace(
            work,
            source_url_fallback=sidecar.source_url_origin == "row",
            source_url_mismatch=sidecar.source_url_mismatch,
        )

        if change.bucket is Bucket.METADATA_CHANGED:
            return self._prepare_metadata(
                work, row, entry, text, sidecar, synced_at, stop
            )

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
            return replace(work, demoted=TOO_MANY_CHUNKS)

        outcome = self._upsert(
            build_document_record(
                row, sidecar, chunk_count=len(chunks), synced_at=synced_at
            ),
            chunks,
            stop,
        )
        return replace(
            work,
            chunk_count=len(chunks),
            outcome=outcome,
            tail=None if entry is None else (entry.chunk_count, len(chunks)),
        )

    def _prepare_metadata(
        self,
        work: _DocumentWork,
        row: DocumentRef,
        entry: ManifestDocumentEntry | None,
        text: str,
        sidecar: ParsedSidecar,
        synced_at: str,
        stop: threading.Event,
    ) -> _DocumentWork:
        """metadata_changed: the record only, or a full re-push where the
        sink cannot merge (L8). Asked of the declared capability rather than
        tried and caught, so the expensive path is a decision, not an
        accident."""
        # Narrowing only: DocumentDiff refuses metadata_changed with no entry.
        assert entry is not None
        if self._sink.capabilities.supports_metadata_only_merge:
            outcome = self._upsert(
                build_document_record(
                    row, sidecar, chunk_count=entry.chunk_count, synced_at=synced_at
                ),
                None,
                stop,
            )
            return replace(work, outcome=outcome)

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
            return replace(work, unconfirmed=True)
        outcome = self._upsert(
            build_document_record(
                row, sidecar, chunk_count=len(chunks), synced_at=synced_at
            ),
            chunks,
            stop,
        )
        return replace(work, metadata_full_repush=True, outcome=outcome)

    def _upsert(
        self,
        record: DocumentRecord,
        chunks: Sequence[Chunk] | None,
        stop: threading.Event,
    ) -> UpsertOutcome:
        """The one place a worker writes to the destination, and the last
        point at which a failing run can still keep it from writing."""
        if stop.is_set():
            raise _RunStopped(record.document_id)
        return self._sink.upsert_document(record, chunks)

    # -- one document, on the coordinator thread ---------------------------

    def _apply(
        self, work: _DocumentWork, progress: _Progress, journal: DeletionJournal
    ) -> None:
        """Fold one prepared document into the run's state, and delete its
        tail once its upsert landed. Coordinator thread only."""
        document_id = work.document_id
        if work.read_skipped:
            progress.read_skipped.add(document_id)
        if work.change is None:
            # classify_document's only None: a deletion signal, never published.
            progress.ended_unpublished.add(document_id)
        progress.source_url_fallback += int(work.source_url_fallback)
        progress.source_url_mismatch += int(work.source_url_mismatch)
        progress.metadata_full_repush += int(work.metadata_full_repush)
        if work.demoted is not None:
            progress.demoted[document_id] = work.demoted
        if work.chunk_count is not None:
            progress.chunk_counts[document_id] = work.chunk_count
        if work.unconfirmed:
            progress.publish_failed.add(document_id)
            progress.unconfirmed.add(document_id)
            progress.publish_failures[document_id] = RECHUNK_COUNT_MISMATCH
        progress.changes.append(work.change)
        if (
            work.outcome is not None
            and self._note_upsert(work.outcome, progress)
            and work.tail is not None
        ):
            # Only after the upsert landed: until then the record still says
            # the old count, and those ordinals are what it points at.
            self._delete_tail(document_id, *work.tail, progress, journal)

    def _note_upsert(self, outcome: UpsertOutcome, progress: _Progress) -> bool:
        """Account for one upsert (F18) and watch for a broken destination
        (F19). True when the document published."""
        progress.upsert_outcomes[outcome.status.value] += 1
        if outcome.published:
            progress.published.add(outcome.document_id)
            progress.consecutive_permanent = 0
            return True
        progress.publish_failed.add(outcome.document_id)
        progress.publish_failures[outcome.document_id] = outcome.status.value
        logger.warning(
            "document %s not published: %s (%s); its previous manifest entry "
            "is kept and it is retried next run",
            outcome.document_id,
            outcome.status.value,
            outcome.detail or "-",
        )
        if outcome.status is UpsertStatus.FAILED_PERMANENT:
            progress.consecutive_permanent += 1
            if progress.consecutive_permanent >= self._abort_threshold:
                progress.aborted = True
                raise SystemicSinkFailure(
                    f"{progress.consecutive_permanent} consecutive permanent sink "
                    f"failures (SINK_FAILURE_ABORT_THRESHOLD="
                    f"{self._abort_threshold}); the destination itself is "
                    "failing, so the run aborts with nothing committed"
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

    def _next_manifest(
        self,
        previous: Manifest | None,
        progress: _Progress,
        watermark: AgencyWatermark,
        *,
        advance_watermark: bool,
    ) -> tuple[DiffResult, Manifest]:
        """The manifest describing `progress`, for the final commit or a
        first-run checkpoint, which therefore cannot drift apart. An entry
        left UNCONFIRMED_FINGERPRINT always withholds the watermark."""
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
            agency_id=self.agency_id,
            diff=diff,
            documents=documents,
            watermark=watermark,
            previous=previous,
            advance_watermark=advance_watermark
            and not any(
                entry.chunker_fingerprint == UNCONFIRMED_FINGERPRINT
                for entry in documents.values()
            ),
            committed_at=self._clock(),
        )
        return diff, manifest

    def _commit(self, manifest: Manifest, marker: BootstrapMarker) -> None:
        """Commit `manifest`, then retire the bootstrap marker (F21): a
        manifest now records what was published, so the marker's claim is
        no longer needed."""
        self._manifests.commit(manifest)
        try:
            marker.clear()
        except OSError as exc:
            # Best effort here only because the commit has landed and must not
            # be reported as failed. The marker is not left to chance: every
            # run that finds a manifest clears it before anything else, and
            # fails if it cannot (_run).
            logger.warning(
                "the bootstrap marker at %s could not be removed after the "
                "commit: %s; the next run removes it or fails",
                marker.path,
                type(exc).__name__,
            )

    def _maybe_checkpoint(
        self,
        previous: Manifest | None,
        progress: _Progress,
        watermark: AgencyWatermark,
        marker: BootstrapMarker,
    ) -> None:
        """F15: commit the manifest so far, on a first run only, once
        BOOTSTRAP_CHECKPOINT_EVERY more documents have published since the
        last checkpoint. Counting published documents rather than rows means
        a fresh CKB that is mostly skipped does not commit the same manifest
        over and over.

        `previous is None` is the whole rule, and it lives only here: on an
        incremental run a partial commit could drop carried-forward entries.
        """
        if previous is not None:
            return
        if (
            len(progress.published) - progress.published_at_checkpoint
            < self._checkpoint_every
        ):
            return
        diff, manifest = self._next_manifest(
            None, progress, watermark, advance_watermark=False
        )
        if diff.deleted or diff.chunks_to_delete:
            # Impossible on a first run: nothing was published before, so
            # nothing can be deleted. Refusing keeps a checkpoint from ever
            # recording a deletion the journal does not yet own.
            raise RuntimeError("a first-run checkpoint found deletions; refusing")
        self._commit(manifest, marker)
        progress.published_at_checkpoint = len(progress.published)
        progress.checkpoints_committed += 1
        logger.info(
            "first-run checkpoint %d committed: %d document(s) published so far",
            progress.checkpoints_committed,
            len(progress.published),
        )

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

    def _dead_letter_size(self) -> int:
        """The dead-letter record's current size, for a run that does not
        update it. Observability only, so never raises."""
        try:
            return len(DeadLetterStore(self._work_dir, agency_id=self.agency_id).load())
        except Exception as exc:
            logger.warning(
                "the dead-letter record could not be read: %s", type(exc).__name__
            )
            return 0

    def _record_dead_letters(
        self, run_id: str, diff: DiffResult, progress: _Progress
    ) -> int:
        """E9: update the dead-letter record after the commit, and return its
        size. Documents skipped by a read-time guard or MAX_CHUNKS_PER_DOCUMENT
        are dead-lettered; see services/dead_letter.py. After the commit, so
        nothing here may fail the run."""
        reasons = diff.skipped_reasons()
        dead = {
            document_id: reasons[document_id]
            for document_id in progress.read_skipped | progress.demoted.keys()
            if document_id in reasons
        }
        resolved = progress.ended_unpublished | {
            change.document_id
            for bucket in (
                diff.new,
                diff.content_changed,
                diff.metadata_changed,
                diff.unchanged,
                diff.deleted,
            )
            for change in bucket
        }
        try:
            store = DeadLetterStore(self._work_dir, agency_id=self.agency_id)
            record = store.update(
                dead,
                resolved=resolved,
                complete=progress.observed_full_corpus,
                run_id=run_id,
                at=self._clock(),
            )
        except Exception as exc:
            logger.warning(
                "run %s: the dead-letter record was not updated: %s",
                run_id,
                type(exc).__name__,
            )
            return len(dead)
        return len(record)

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

    def _report(
        self,
        run_id: str,
        outcome: RunOutcome,
        started_at: str,
        *,
        error_type: str | None = None,
    ) -> RunReport:
        return RunReport(
            run_id=run_id,
            agency_id=self.agency_id,
            sink_id=self._sink.sink_id,
            outcome=outcome,
            started_at=started_at,
            finished_at=self._clock(),
            error_type=error_type,
        )

    def _observe(self, report: RunReport) -> None:
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
            duration_seconds=report.duration_seconds,
            counts=counts,
            deletions_recorded=report.deletions_recorded,
            error_type=report.error_type,
            upsert_outcomes=report.upsert_outcomes,
            full_corpus=report.run_observed_full_corpus,
            dead_lettered=report.dead_lettered_count,
            stale_sweep=report.stale_sweep,
            rechunk=report.rechunk,
        )
        if self._run_state is not None:
            self._run_state.record(
                outcome=outcome,
                run_id=report.run_id,
                agency_id=report.agency_id,
                duration_seconds=report.duration_seconds,
                error_type=report.error_type,
                run_observed_full_corpus=report.run_observed_full_corpus,
                stale_sweep=report.stale_sweep,
                stale_swept_count=report.stale_swept_count,
                rechunk=report.rechunk,
            )


def _outcome_fields(progress: _Progress) -> dict[str, object]:
    """The report fields a run fills in whether it succeeded or failed: what
    it committed (F15) and what each upsert came to (F18). Every status is
    present, as 0 when nothing had it, so a report always reads the same."""
    return {
        "checkpoints_committed": progress.checkpoints_committed,
        "publish_failed_count": len(progress.publish_failed),
        "upsert_outcomes": {
            status.value: progress.upsert_outcomes[status.value]
            for status in UpsertStatus
        },
        "publish_failures": dict(sorted(progress.publish_failures.items())),
        "first_run_probe": progress.first_run_probe,
        "rechunk": progress.rechunk,
    }


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


def _check_read(row: DocumentRef, read: ReadDocument) -> None:
    """The read contract schemas.ReadDocument states, which the coordinator
    checks because the type does not.

    The source may add hashes or a skip reason to a row, and nothing else:
    a read that came back as another document, or with the row's own fields
    changed, would publish something the enumeration never described. A
    skipped read carries no text and no sidecar; any other read carries
    NFC text, its sidecar and all three hashes, the metadata hash being the
    sidecar's own. Messages name the document and never its text (F13)."""
    document_id = row.document_id
    ref = read.ref
    if ref.skip_reason is not None:
        if read.text is not None or read.sidecar is not None:
            raise ValueError(
                f"document {document_id}: a skipped read carries no text and no sidecar"
            )
    else:
        if read.text is None or read.sidecar is None:
            raise ValueError(
                f"document {document_id}: a read that was not skipped needs "
                "its text and its sidecar"
            )
        if None in (ref.raw_sha256, ref.content_sha256, ref.metadata_sha256):
            raise ValueError(f"document {document_id}: a read document is hashed")
        if ref.metadata_sha256 != read.sidecar.metadata_sha256:
            raise ValueError(
                f"document {document_id}: metadata_sha256 does not match the "
                "sidecar it was read with"
            )
        if not unicodedata.is_normalized("NFC", read.text):
            raise ValueError(f"document {document_id}: read text is not NFC")
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
