"""The deferred-deletion drain (J1, J2): retry the whole-document deletes
earlier runs journalled and the destination did not confirm.

WHAT IT READS. Every journal on CONTENT_WORK_DIR with a line for the agency,
through services/deletion_journal.py's pending fold: a document is pending
when its latest line is an intent, or an outcome naming it unresolved.

WHAT IT LEAVES ALONE, AND WHY.

- Journals no committed run has closed. Such a run died before its commit,
  so everything it tried to delete is still in the committed manifest; the
  next export distrusts those entries, republishes or re-deletes them, and
  closes the journal. Counted as `awaiting_export`.
- Documents the committed manifest lists again. Restored and re-published
  since the delete was journalled: deleting one now would remove live
  content — the sequential form of flagged risk 8, which the run lock alone
  does not prevent. Counted as `skipped_republished`. This re-check reads the
  manifest fail-closed (C9, D20): no manifest, an unreadable one or one for
  another sink fails the drain, because without it no candidate can be
  vouched for. The manifest is read only when a closed journal holds a
  pending delete: with nothing to re-check there is nothing to vouch for, so
  a deployment whose first export has not committed yet reports
  `nothing_pending`, not `failed`, on every tick.
- Tail (`chunks`) entries. diff.record() keeps a document whose tail was not
  confirmed on its previous, larger chunk_count, so every journalled tail
  ordinal is below the count the manifest holds now — the re-check the
  journal's contract requires would skip all of them — and the next export
  recomputes and retries that tail by itself. Counted as
  `tails_left_to_export`. Reconcile's orphan tails (J5) are counted apart, as
  `tails_left_to_reconcile`: no manifest ever counted those ordinals, so no
  export retries them, and the drain cannot re-check them against anything
  but a listing. The next `reconcile --repair` finds them again.

WHERE IT WRITES. Into the journal it drains: an intent, then an outcome, each
line marked `drained_by`, so "the latest line wins" stays the one definition
of pending and a later drain sees what this one did. The journal's length then
differs from its mirror, so the drain mirrors it again itself — a closed
journal left needing a mirror would keep the next export off Gate 1's
shortcut until it did. Every non-dry drain mirrors every closed journal that
needs it, not only the ones it appended to, so a drain that died between its
deletes and its mirror is caught up by the next drain.

DRY RUN (J2). Computes the candidates exactly as a real drain would and
writes nothing: no journal line, no sink call, no mirror. POST
/drain_deletions runs dry unless DRAIN_DELETES_ENABLED is set, so a deployment
inspects one full cycle of journals before anything is deleted.
"""

import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from exporter.services.deletion_dispatch import (
    close_and_mirror_journals,
    delete_documents_journalled,
)
from exporter.services.deletion_journal import (
    ORPHAN_TAIL_REASON,
    DeletionJournal,
    agency_journals,
)
from exporter.services.manifest_service import ManifestService
from exporter.services.operations import (
    MAX_REPORTED_IDS,
    DrainReport,
    OperationStateStore,
    run_operation,
)
from exporter.services.run_state import utc_now_iso
from exporter.sinks.base import ContentSink

__all__ = ["DeletionDrain", "NoManifestToDrainError"]

logger = logging.getLogger(__name__)


class NoManifestToDrainError(RuntimeError):
    """No committed manifest, so no pending delete can be re-checked
    against what is live. Fail closed rather than delete blind."""


class DeletionDrain:
    """Drains one agency's journals into one sink."""

    def __init__(
        self,
        *,
        sink: ContentSink,
        manifests: ManifestService,
        work_dir: Path,
        state: OperationStateStore | None = None,
        clock: Callable[[], str] = utc_now_iso,
    ) -> None:
        if manifests.sink_id != sink.sink_id:
            raise ValueError(
                "the manifest service and the sink name different destinations"
            )
        self._sink = sink
        self._manifests = manifests
        self._work_dir = work_dir
        self._state = state
        self._clock = clock

    @property
    def agency_id(self) -> str:
        return self._sink.agency_id

    def drain(self, run_id: str, *, dry_run: bool) -> DrainReport:
        """One drain. Every outcome is a report, never a raise: `busy` when
        an export or another operation holds the run lock, `failed` on any
        error, with whatever was already journalled standing."""
        started_at = self._clock()

        def report(outcome: str, **fields: object) -> DrainReport:
            base = DrainReport(
                run_id=run_id,
                agency_id=self.agency_id,
                sink_id=self._sink.sink_id,
                outcome=outcome,
                started_at=started_at,
                finished_at=self._clock(),
                dry_run=dry_run,
            )
            return replace(base, **fields)

        return run_operation(
            "drain",
            work_dir=self._work_dir,
            run_id=run_id,
            body=lambda: self._drain(run_id, dry_run=dry_run, report=report),
            on_busy=lambda: report("busy"),
            on_error=lambda exc: report("failed", error_type=type(exc).__name__),
            state=self._state,
        )

    def _drain(
        self,
        run_id: str,
        *,
        dry_run: bool,
        report: Callable[..., DrainReport],
    ) -> DrainReport:
        agency_id = self.agency_id
        journals = agency_journals(self._work_dir, agency_id)
        closed = [journal for journal in journals if journal.is_closed()]
        pending = [(journal, journal.pending_document_deletes()) for journal in closed]
        counts: dict[str, object] = {
            "journals_scanned": len(closed),
            "awaiting_export": len(journals) - len(closed),
            "tails_left_to_export": sum(j.pending_chunk_entries() for j in closed),
            "tails_left_to_reconcile": sum(
                j.pending_chunk_entries(reason=ORPHAN_TAIL_REASON) for j in closed
            ),
        }
        if not any(deletes for _, deletes in pending):
            # Nothing to re-check, so nothing needs the manifest: a deployment
            # whose first export has not committed yet has nothing to drain,
            # and that is not a failure on every tick.
            if dry_run:
                return report("dry_run", **counts)
            # A drain that died after appending, before its mirror, left a
            # closed journal needing one; mirror it rather than wait for the
            # next export to lose Gate 1's shortcut over it.
            close_and_mirror_journals(run_id, [], closed, self._manifests)
            return report("nothing_pending", **counts)

        manifest = self._manifests.load_previous(agency_id)
        if manifest is None:
            raise NoManifestToDrainError(
                f"agency {agency_id}: no committed manifest to re-check pending "
                "deletes against; refusing to delete blind"
            )
        work: list[tuple[DeletionJournal, dict[str, str]]] = []
        republished = 0
        for journal, deletes in pending:
            live = deletes.keys() & manifest.documents.keys()
            republished += len(live)
            candidates = {
                document_id: reason
                for document_id, reason in sorted(deletes.items())
                if document_id not in live
            }
            if candidates:
                work.append((journal, candidates))

        candidate_ids = sorted({d for _, candidates in work for d in candidates})
        counts |= {
            "candidates": len(candidate_ids),
            "skipped_republished": republished,
            "candidate_ids": tuple(candidate_ids[:MAX_REPORTED_IDS]),
        }
        if dry_run:
            logger.info(
                "drain %s: dry run, %d pending delete(s) would be retried",
                run_id,
                len(candidate_ids),
            )
            return report("dry_run", **counts)
        if not work:
            close_and_mirror_journals(run_id, [], closed, self._manifests)
            return report("nothing_pending", **counts)
        return report("drained", **self._retry(run_id, work, closed), **counts)

    def _retry(
        self,
        run_id: str,
        work: list[tuple[DeletionJournal, dict[str, str]]],
        closed: list[DeletionJournal],
    ) -> dict[str, object]:
        """Delete each journal's candidates, journalled into that journal,
        then mirror every closed journal not mirrored at its current length:
        the ones this drain appended to, and any an earlier drain appended to
        but died before mirroring."""
        deleted = missing = unresolved = 0
        for journal, candidates in work:
            outcome = delete_documents_journalled(
                self._sink, journal, candidates, drained_by=run_id
            )
            deleted += len(outcome.deleted)
            missing += len(outcome.missing)
            unresolved += len(outcome.unresolved)
        # Nothing to close: every journal here already is. Mirror only reads
        # needs_mirror(), so a journal already mirrored at its length is
        # skipped.
        close_and_mirror_journals(run_id, [], closed, self._manifests)
        logger.info(
            "drain %s: %d deleted, %d already gone, %d still unresolved",
            run_id,
            deleted,
            missing,
            unresolved,
        )
        return {"deleted": deleted, "missing": missing, "still_unresolved": unresolved}
