"""Reconcile (J4, J9) and the orphan tail sweep (J5): compare what the
destination holds with what the committed manifest says it holds, report the
drift, and — when asked, on a sink that can list — remove orphaned tail
chunks.

WHY IT EXISTS. Every other mechanism in this service trusts its own record:
the diff trusts the manifest, the drain trusts the journal. Reconcile is the
one that looks at the destination, so it is the only thing that can catch
drift none of them sees — a delete that half-landed before a crash, a chunk
written past a count nothing recorded, an object someone put there by hand.

HOW MUCH IT CAN SEE IS A PROPERTY OF THE SINK, reported as `scope`:

- `full`: the sink can list (`list_documents`). Every document the
  destination holds is seen, including ones the manifest does not know.
- `manifest_documents`: the sink can read back but not list. Each document
  the manifest lists is probed; anything else at the destination is
  invisible, so `unrecorded_documents` is not measured. No drift here is
  `clean_partial`, never `clean`: `clean` is reserved for a comparison that
  saw the whole destination, so an alert keyed on it cannot be satisfied by
  one that did not.
- `none`: neither. The outcome is `unverifiable` — never `clean` (J9). A
  clean bill from a comparison that never happened is worse than none,
  because reconcile exists precisely to catch what nothing else sees. The
  report still carries what the manifest claims.

DRIFT, one count per category: `missing_documents` (listed in the manifest,
nothing at the destination), `record_missing` (chunks with no record — an F3
breach), `chunk_gaps` (an ordinal below the manifest's count is absent),
`orphan_tail_*` (ordinals at or past the count), `unrecorded_documents`
(at the destination, not in the manifest; full scope only).

THE TAIL RULE (J5), AND WHY IT TAKES THE LARGER COUNT. A tail is every
ordinal >= max(the chunk_count the destination's own record declares, the
manifest's chunk_count). The record is written before its chunks (F3), so its
count is the newest the destination holds — the manifest can be behind it,
after an upsert that landed but was not confirmed, and sweeping by the
manifest alone would then delete live chunks. Taking the larger of the two
can only delete less. Its guard against a gateway's pseudo-directory entries
is D12 — every listed key is parsed, never counted — and it needs no
completeness guard: unlike the `synced_at` sweep (L10), it concludes nothing
from what a run did not touch.

REPAIR IS NARROW ON PURPOSE. Only orphan tails, only at `full` scope, only
for a document whose record states its count, and only when asked: the CLI's
--repair, or RECONCILE_REPAIR_ENABLED for the HTTP path. Every other
category stays report-only: a missing document is republished by the next
export that reads its row, and an unrecorded one may be content the manifest
is merely behind on. Each tail delete is journalled first (F7) in this run's
own journal, as ORPHAN_TAIL_REASON, which is closed and mirrored afterwards —
closing is truthful, since the manifest never counted those ordinals — so the
next export does not distrust and republish the repaired documents. A tail
the destination refused stays pending in that journal; the drain reports it
as `tails_left_to_reconcile`, and the next repair retries it from its own
listing.
"""

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from exporter.core.schemas import Manifest, ManifestDocumentEntry
from exporter.services.deletion_dispatch import (
    close_and_mirror_journals,
    delete_chunks_journalled,
)
from exporter.services.deletion_journal import ORPHAN_TAIL_REASON, DeletionJournal
from exporter.services.manifest_service import ManifestService
from exporter.services.operations import (
    MAX_REPORTED_IDS,
    OperationStateStore,
    ReconcileReport,
    run_operation,
)
from exporter.services.run_state import utc_now_iso
from exporter.sinks.base import ContentSink, DocumentProbe

__all__ = ["DRIFT_CATEGORIES", "NoManifestToReconcileError", "Reconciler"]

logger = logging.getLogger(__name__)

DRIFT_CATEGORIES = (
    "missing_documents",
    "record_missing",
    "chunk_gaps",
    "orphan_tails",
    "unrecorded_documents",
)


class NoManifestToReconcileError(RuntimeError):
    """No committed manifest: there is nothing to compare the destination
    with, and a missing manifest is never read as an empty one."""


@dataclass
class _Drift:
    ids: dict[str, list[str]] = field(
        default_factory=lambda: {category: [] for category in DRIFT_CATEGORIES}
    )
    orphan_tail_chunks: int = 0
    # document_id -> the tail ordinals J5 may delete: past both counts, and
    # only where the record states its own.
    repairable: dict[str, list[int]] = field(default_factory=dict)

    def add(self, category: str, document_id: str) -> None:
        self.ids[category].append(document_id)

    def total(self) -> int:
        return sum(len(ids) for ids in self.ids.values())


class Reconciler:
    """Reconciles one agency's destination against its manifest."""

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

    def reconcile(self, run_id: str, *, repair: bool) -> ReconcileReport:
        """One reconcile. Every outcome is a report, never a raise."""
        started_at = self._clock()

        def report(outcome: str, **fields: object) -> ReconcileReport:
            base = ReconcileReport(
                run_id=run_id,
                agency_id=self.agency_id,
                sink_id=self._sink.sink_id,
                outcome=outcome,
                started_at=started_at,
                finished_at=self._clock(),
                repair=repair,
            )
            return replace(base, **fields)

        return run_operation(
            "reconcile",
            work_dir=self._work_dir,
            run_id=run_id,
            body=lambda: self._reconcile(run_id, repair=repair, report=report),
            on_busy=lambda: report("busy"),
            on_error=lambda exc: report("failed", error_type=type(exc).__name__),
            state=self._state,
        )

    def _reconcile(
        self,
        run_id: str,
        *,
        repair: bool,
        report: Callable[..., ReconcileReport],
    ) -> ReconcileReport:
        manifest = self._manifests.load_previous(self.agency_id)
        if manifest is None:
            raise NoManifestToReconcileError(
                f"agency {self.agency_id}: no committed manifest to reconcile "
                "the destination against"
            )
        claimed: dict[str, object] = {
            "manifest_documents": len(manifest.documents),
            "manifest_chunks": sum(e.chunk_count for e in manifest.documents.values()),
        }
        capabilities = self._sink.capabilities
        if capabilities.can_list:
            scope = "full"
            probes = {probe.document_id: probe for probe in self._sink.list_documents()}
        elif capabilities.can_read_back:
            scope = "manifest_documents"
            probes = self._read_back(manifest.documents)
        else:
            logger.info(
                "reconcile %s: the sink can neither list nor read back; "
                "the destination cannot be compared (J9)",
                run_id,
            )
            return report("unverifiable", scope="none", **claimed)

        drift = _compare(manifest, probes, full=scope == "full")
        fields = {**claimed, **_drift_fields(scope, probes, drift)}
        if drift.total() == 0:
            # `clean` means the whole destination was compared. Read-backs
            # saw only the manifest's own documents, so anything else there
            # was never looked at: no drift seen is not a clean bill (J9).
            return report("clean" if scope == "full" else "clean_partial", **fields)
        if not (repair and scope == "full" and drift.repairable):
            if repair and scope != "full":
                logger.info(
                    "reconcile %s: repair needs a listing (scope full), this "
                    "sink's scope is %s; reporting only",
                    run_id,
                    scope,
                )
            return report("drift", **fields)

        repaired, unresolved = self._repair_tails(run_id, drift.repairable)
        remaining = drift.total() - len(repaired)
        return report(
            "repaired" if remaining == 0 else "drift",
            tails_repaired=len(repaired),
            tails_unresolved=len(unresolved),
            **fields,
        )

    def _read_back(
        self, documents: Mapping[str, ManifestDocumentEntry]
    ) -> dict[str, DocumentProbe]:
        """One probe per manifest document, in id order, sequentially: a
        daily read of what the manifest claims, not a throughput test of
        someone else's service."""
        probes: dict[str, DocumentProbe] = {}
        for document_id in sorted(documents):
            probe = self._sink.read_document(document_id)
            if probe is not None:
                probes[document_id] = probe
        return probes

    def _repair_tails(
        self, run_id: str, repairable: Mapping[str, list[int]]
    ) -> tuple[list[str], list[str]]:
        """J5: journal, then delete each document's orphaned tail. Returns
        the documents whose tail is confirmed gone and those still pending."""
        journal = DeletionJournal(
            self._work_dir, run_id=run_id, agency_id=self.agency_id
        )
        if journal.is_closed():
            raise ValueError(
                f"run id {run_id} already committed for this agency; a new "
                "reconcile needs a new id"
            )
        repaired: list[str] = []
        unresolved: list[str] = []
        for document_id, ordinals in sorted(repairable.items()):
            outcome = delete_chunks_journalled(
                self._sink,
                journal,
                document_id,
                ordinals,
                reason=ORPHAN_TAIL_REASON,
            )
            (repaired if outcome.is_complete else unresolved).append(document_id)
        # The manifest never counted these ordinals, so it has caught up with
        # this journal already: close it, so the next export does not
        # distrust the repaired documents, and mirror it. An unresolved tail
        # stays journalled as ORPHAN_TAIL_REASON, which the drain reports as
        # `tails_left_to_reconcile`: no export retries it, and the next
        # repair finds it in the listing again.
        close_and_mirror_journals(run_id, [journal], [journal], self._manifests)
        logger.info(
            "reconcile %s: %d orphaned tail(s) removed, %d unresolved",
            run_id,
            len(repaired),
            len(unresolved),
        )
        return repaired, unresolved


def _drift_fields(
    scope: str, probes: Mapping[str, DocumentProbe], drift: _Drift
) -> dict[str, object]:
    """The report's view of what was seen: the scope, one count per drift
    category, and up to MAX_REPORTED_IDS ids of each (JSON report only)."""
    return {
        "scope": scope,
        "destination_documents": len(probes),
        **{
            category: len(ids)
            for category, ids in drift.ids.items()
            if category != "orphan_tails"
        },
        "orphan_tail_documents": len(drift.ids["orphan_tails"]),
        "orphan_tail_chunks": drift.orphan_tail_chunks,
        "drift_ids": {
            category: tuple(ids[:MAX_REPORTED_IDS])
            for category, ids in drift.ids.items()
            if ids
        },
    }


def _compare(
    manifest: Manifest, probes: Mapping[str, DocumentProbe], *, full: bool
) -> _Drift:
    drift = _Drift()
    for document_id, entry in sorted(manifest.documents.items()):
        probe = probes.get(document_id)
        if probe is None:
            drift.add("missing_documents", document_id)
            continue
        if not probe.record_present:
            drift.add("record_missing", document_id)
        _compare_chunks(drift, document_id, entry.chunk_count, probe)
    if full:
        for document_id in sorted(probes.keys() - manifest.documents.keys()):
            drift.add("unrecorded_documents", document_id)
    return drift


def _compare_chunks(
    drift: _Drift, document_id: str, manifest_count: int, probe: DocumentProbe
) -> None:
    if probe.chunk_ordinals is None:
        # A sink that can only count: a shortfall is a gap, a surplus a
        # tail, and neither can be located, so neither is repairable.
        if probe.chunk_count < manifest_count:
            drift.add("chunk_gaps", document_id)
        elif probe.chunk_count > manifest_count:
            drift.add("orphan_tails", document_id)
            drift.orphan_tail_chunks += probe.chunk_count - manifest_count
        return
    present = set(probe.chunk_ordinals)
    if not set(range(manifest_count)) <= present:
        drift.add("chunk_gaps", document_id)
    declared = probe.declared_chunk_count
    tail = _tail(present, max(manifest_count, declared or 0))
    if not tail:
        return
    drift.add("orphan_tails", document_id)
    drift.orphan_tail_chunks += len(tail)
    if probe.record_present and declared is not None:
        drift.repairable[document_id] = tail


def _tail(present: Iterable[int], start: int) -> list[int]:
    return sorted(ordinal for ordinal in present if ordinal >= start)
