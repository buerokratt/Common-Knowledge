"""The purge (J7, J8): delete everything the committed manifest records for
one agency, on an operator's explicit request.

NOT A SECOND CODE PATH. A purge is the diff with an empty `current`:
classify(previous, [], fingerprint) puts every published document in
`deleted` / `absent_from_rows`, and the deletes go through the sink like any
other (F5) — journalled first (F7), with no per-sink branch. That is the
maintainability payoff of Stage C's shape, and the reason C9's fail-closed
manifest read exists: "everything is deleted" must never be reachable from a
manifest that failed to load.

ITS GUARDS, AND THE ONE IT DOES NOT NEED.

- Operator-invoked only. There is no HTTP route; `python -m exporter.run_ops
  purge` is the one entry point, and it refuses to start unless --confirm
  repeats the agency id. Nothing schedules it, and nothing can.
- The manifest is read fail-closed. No manifest is `failed`, not "nothing to
  purge": a missing manifest is never read as an empty one.
- The diff must be a purge (`DiffResult.is_purge`) — every published
  document deleted and nothing else happening.
- It does not consult `run_observed_full_corpus` (F20). That flag guards
  conclusions drawn from what an export did NOT see; a purge draws none — it
  never enumerates CKB, it is told. The export's refusal to act on an empty
  enumeration (EmptyEnumerationError) is what keeps a purge from ever being
  produced by accident.

THE RESIDUE. After the recorded documents, a sink that declares
`supports_agency_scoped_delete` is asked to delete the agency outright, for
anything the manifest never recorded — a document published by a run that
died before its commit, say. Then, on a sink that can list, the destination is
checked empty (`verified_empty`); on one that cannot, residual state cannot be
confirmed absent and the report says `unverifiable` beside the requested and
acknowledged counts (J9). The outcome carries the same distinction: `purged`
only for a destination confirmed empty, `purged_unverified` when everything
requested was acknowledged but nothing could check what is left.

THE MANIFEST AFTERWARDS. A manifest with no documents and no corpus watermark
is committed, so the purge is recorded and the next export walks every row.
Deletes the destination did not confirm stay in this run's journal for the
drain. The purge does not read CKB: the committed document_count is 0.

A PURGE IS UNDONE BY THE NEXT EXPORT if CKB still holds the agency's rows:
the export republishes every finished document. Disable the export's
schedule first (RUNBOOK: Purging an agency).
"""

import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from exporter.core.diff import DiffResult, classify, record
from exporter.core.redaction import sanitize_sensitive_text
from exporter.core.schemas import AgencyWatermark, Manifest
from exporter.services.deletion_dispatch import (
    close_and_mirror_journals,
    delete_documents_journalled,
)
from exporter.services.deletion_journal import DeletionJournal, agency_journals
from exporter.services.manifest_service import ManifestService
from exporter.services.operations import (
    MAX_REPORTED_IDS,
    OperationStateStore,
    PurgeReport,
    run_operation,
)
from exporter.services.run_state import utc_now_iso
from exporter.sinks.base import ContentSink, NotSupported, StoreError
from exporter.stores.base import DeleteOutcome

__all__ = ["NoManifestToPurgeError", "Purger"]

logger = logging.getLogger(__name__)

# What the purge records as nothing this deployment can know about CKB.
_NO_ROWS_READ = AgencyWatermark(max_updated_at=None, live_count=0)


class NoManifestToPurgeError(RuntimeError):
    """No committed manifest. Never "nothing to purge": a missing manifest is
    never read as an empty one (C9)."""


class Purger:
    """Purges one agency from one sink."""

    def __init__(
        self,
        *,
        sink: ContentSink,
        manifests: ManifestService,
        chunker_fingerprint: str,
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
        self._fingerprint = chunker_fingerprint
        self._work_dir = work_dir
        self._state = state
        self._clock = clock

    @property
    def agency_id(self) -> str:
        return self._sink.agency_id

    def purge(self, run_id: str, *, confirm: str) -> PurgeReport:
        """One purge. `confirm` must repeat the bound agency id — checked
        again here, so no caller can reach the deletes without it. Every
        outcome after that is a report, never a raise."""
        if confirm != self.agency_id:
            raise ValueError(
                "a purge must be confirmed with the agency id it deletes; "
                f"got {confirm!r} for agency {self.agency_id!r}"
            )
        started_at = self._clock()

        def report(outcome: str, **fields: object) -> PurgeReport:
            base = PurgeReport(
                run_id=run_id,
                agency_id=self.agency_id,
                sink_id=self._sink.sink_id,
                outcome=outcome,
                started_at=started_at,
                finished_at=self._clock(),
            )
            return replace(base, **fields)

        return run_operation(
            "purge",
            work_dir=self._work_dir,
            run_id=run_id,
            body=lambda: self._purge(run_id, report=report),
            on_busy=lambda: report("busy"),
            on_error=lambda exc: report("failed", error_type=type(exc).__name__),
            state=self._state,
        )

    def _purge(self, run_id: str, *, report: Callable[..., PurgeReport]) -> PurgeReport:
        agency_id = self.agency_id
        journal = DeletionJournal(self._work_dir, run_id=run_id, agency_id=agency_id)
        if journal.is_closed():
            raise ValueError(
                f"run id {run_id} already committed for this agency; a new "
                "purge needs a new id"
            )
        previous = self._manifests.load_previous(agency_id)
        if previous is None:
            raise NoManifestToPurgeError(
                f"agency {agency_id}: no committed manifest; nothing is recorded "
                "to purge, and a missing manifest is not an empty one"
            )
        diff = classify(previous, [], self._fingerprint)
        if previous.documents and not diff.is_purge:
            # classify() with no rows can only delete; anything else is a bug
            # in the diff, and acting on it is not a purge.
            raise ValueError("an empty classification did not produce a purge")

        reasons = {change.document_id: change.reason or "" for change in diff.deleted}
        outcome = delete_documents_journalled(self._sink, journal, reasons)
        residue = self._delete_residue()
        self._commit_empty(run_id, journal, diff, previous)
        residual = self._residual()

        fields: dict[str, object] = {
            "requested": len(reasons),
            "deleted": len(outcome.deleted),
            "missing": len(outcome.missing),
            "unresolved": len(outcome.unresolved),
            "deletions_recorded": journal.intents_recorded,
            "residue": residue,
            "residual": residual,
            "unresolved_ids": tuple(sorted(outcome.unresolved)[:MAX_REPORTED_IDS]),
        }
        if not reasons and residue in ("missing", "not_supported", "not_run"):
            return report("nothing_to_purge", **fields)
        logger.info(
            "purge %s: %d of %d recorded document(s) confirmed gone, residue %s, "
            "destination %s",
            run_id,
            len(outcome.succeeded),
            len(reasons),
            residue,
            residual,
        )
        return report(_purge_outcome(outcome, residue, residual), **fields)

    def _commit_empty(
        self,
        run_id: str,
        journal: DeletionJournal,
        diff: DiffResult,
        previous: Manifest,
    ) -> None:
        """Commit the manifest with no documents (F6: the purge is recorded),
        then close and mirror this run's journal and any an earlier run left
        open — after the commit, never before."""
        now = self._clock()
        manifest = self._manifests.build(
            agency_id=self.agency_id,
            diff=diff,
            documents=record(diff, set(), tails_deleted=set(), processed_at=now),
            watermark=_NO_ROWS_READ,
            previous=previous,
            advance_watermark=False,
            committed_at=now,
        )
        self._manifests.commit(manifest)
        on_disk = agency_journals(self._work_dir, self.agency_id)
        unclosed = [j for j in on_disk if not j.is_closed()]
        close_and_mirror_journals(
            run_id, [*unclosed, journal], [*on_disk, journal], self._manifests
        )

    def _delete_residue(self) -> str:
        """The agency-scoped delete, where the sink has one."""
        if not self._sink.capabilities.supports_agency_scoped_delete:
            return "not_supported"
        try:
            outcome = self._sink.delete_agency(self.agency_id)
        except NotSupported:
            return "not_supported"
        except StoreError as exc:
            # The recorded documents are already handled and journalled; a
            # residue that could not be reached must not stop the purge being
            # recorded. Reported, and the report is `incomplete`.
            logger.warning(
                "purge: the agency-scoped delete failed: %s: %s",
                type(exc).__name__,
                sanitize_sensitive_text(str(exc)),
            )
            return "failed"
        if outcome.failed:
            return "failed"
        if outcome.deferred:
            return "deferred"
        return "deleted" if outcome.deleted else "missing"

    def _residual(self) -> str:
        """Whether the destination is confirmed empty for the agency."""
        if not self._sink.capabilities.can_list:
            return "unverifiable"
        try:
            held = self._sink.holds_agency_content()
        except Exception as exc:
            # After the commit: a check that cannot run is reported, never
            # allowed to turn a recorded purge into a failed one.
            logger.warning(
                "purge: could not check the destination is empty: %s",
                type(exc).__name__,
            )
            return "unverifiable"
        return "not_empty" if held else "verified_empty"


def _purge_outcome(outcome: DeleteOutcome[str], residue: str, residual: str) -> str:
    """`incomplete` when anything requested or the residue was not
    confirmed, or the destination was seen still holding content. Otherwise
    `purged` only when the destination was confirmed empty: on a sink that
    cannot check, every acknowledged delete is still not a verified purge,
    and the outcome says so (J9) — `purged_unverified`."""
    if (
        not outcome.is_complete
        or residue in ("failed", "deferred")
        or residual == "not_empty"
    ):
        return "incomplete"
    return "purged" if residual == "verified_empty" else "purged_unverified"
