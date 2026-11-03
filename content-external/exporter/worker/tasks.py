"""The export task, shared by the HTTP endpoint (F9) and the CLI (F10).

`export_agency_task` is one run end to end: the authoritative N=1 check
(A17), the coordinator built from settings, and the export itself. Anything
that fails before the coordinator can report it is reported by `catch_error`
(F12), so a configuration refusal is a `failed` run in /health and in the
alert query, not a stack trace in a spawned child's stderr.

`run_export_process` is the `multiprocessing.Process` target the endpoint
starts, as cleaning/api/app.py does for `/clean_source_async`. It exits 1 on
`failed` and 0 otherwise — `busy` included, which is by design not an error —
and it never lets a traceback reach stderr (F13).

`operation_task` and `run_operation_process` are the same pair for the
deletion operations (Stage J): the drain, reconcile and the purge. They share
the A17 check and the wiring, and report a failure before the operation could
run as that operation's `failed` outcome — not the export's.
"""

import logging
import secrets
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO, get_args

from exporter.api.config import Settings
from exporter.core.ids import validate_segment
from exporter.core.schemas import RunReport
from exporter.services.agency_guard import require_single_agency
from exporter.services.drain_service import DeletionDrain
from exporter.services.operations import (
    AnyReport,
    DrainReport,
    Operation,
    OperationStateStore,
    PurgeReport,
    ReconcileReport,
    log_operation_outcome,
)
from exporter.services.purge_service import Purger
from exporter.services.reconcile_service import Reconciler
from exporter.services.run_state import RunStateStore, utc_now_iso
from exporter.worker.utils import catch_error, log_error_line
from exporter.worker.wiring import (
    SourceFactory,
    build_coordinator,
    build_deletion_context,
    build_document_source,
)

__all__ = [
    "ExportTask",
    "OperationTask",
    "configure_logging",
    "export_agency_task",
    "new_run_id",
    "operation_task",
    "run_export_process",
    "run_operation_process",
]

logger = logging.getLogger(__name__)

LOG_FORMAT = "%(levelname)s - %(asctime)s - %(name)s - %(message)s"


@dataclass(frozen=True)
class ExportTask:
    """What one export was asked to do."""

    agency_id: str
    run_id: str
    force: bool = False
    limit: int | None = None
    # F21's override: a first run may publish into a destination that already
    # holds this agency's content. Only the CLI sets it; the HTTP body has no
    # such field, so the cron path can never reach it.
    accept_existing_content: bool = False
    # L18's override: a pending re-chunk may run on a destination that
    # re-embeds every chunk it receives. CLI only, like the one above.
    accept_rechunk: bool = False

    def __post_init__(self) -> None:
        # Both become path segments: the run id names the journal file, the
        # agency id every key. Refused here as well as at each entry point,
        # so no caller can hand the coordinator one that escapes its folder.
        validate_segment("agency_id", self.agency_id)
        validate_segment("run_id", self.run_id)
        if self.limit is not None and (isinstance(self.limit, bool) or self.limit < 1):
            raise ValueError(f"limit must be >= 1, got {self.limit!r}")


def new_run_id() -> str:
    """A run id for a caller that did not supply one: the UTC start time,
    sortable, plus a random suffix so two runs in one second differ."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(4)}"


def configure_logging(stream: TextIO | None = None) -> None:
    """The service's log format, on `stream` (stdout by default). Idempotent
    where it matters: basicConfig does nothing once the root logger has a
    handler, which is the case in a forked child; a spawned child or the CLI
    gets one here."""
    logging.basicConfig(
        level=logging.INFO,
        format=LOG_FORMAT,
        stream=stream if stream is not None else sys.stdout,
    )


def export_agency_task(
    task: ExportTask,
    settings: Settings,
    work_dir: Path,
    *,
    run_state: RunStateStore | None = None,
    source_factory: SourceFactory = build_document_source,
) -> RunReport:
    """One export run. Raises only for a failure before the coordinator
    could report one, and that failure has already been reported (F12)."""
    run_state = run_state if run_state is not None else RunStateStore(work_dir)
    with catch_error(
        run_id=task.run_id,
        agency_id=task.agency_id,
        sink=settings.sink_id,
        run_state=run_state,
    ):
        # A17, authoritative: a second agency would starve behind the
        # whole-run lock in silence, so it is a failed run instead.
        require_single_agency(settings)
        coordinator = build_coordinator(
            settings,
            agency_id=task.agency_id,
            run_id=task.run_id,
            work_dir=work_dir,
            run_state=run_state,
            source_factory=source_factory,
        )
    return coordinator.export(
        task.run_id,
        force=task.force,
        limit=task.limit,
        accept_existing_content=task.accept_existing_content,
        accept_rechunk=task.accept_rechunk,
    )


def run_export_process(task: ExportTask, settings: Settings, work_dir: Path) -> None:
    """The `multiprocessing` target. Exits 1 on `failed`, 0 otherwise."""
    configure_logging()
    try:
        report = export_agency_task(task, settings, work_dir)
    except Exception:
        # Already reported by catch_error. Exiting rather than re-raising is
        # what keeps multiprocessing from printing a traceback (F13).
        raise SystemExit(1) from None
    if report.outcome == "failed":
        raise SystemExit(1)


# --- the deletion operations (Stage J) ----------------------------------------


@dataclass(frozen=True)
class OperationTask:
    """What one deletion operation was asked to do. Each flag belongs to one
    operation and is refused on the others, so a request can never carry a
    switch that silently does nothing."""

    operation: Operation
    agency_id: str
    run_id: str
    # drain (J2): report what would be retried, delete nothing.
    dry_run: bool = False
    # reconcile (J5): remove orphaned tails, not only report them.
    repair: bool = False
    # purge (J8): must repeat agency_id.
    confirm: str | None = None

    def __post_init__(self) -> None:
        if self.operation not in get_args(Operation):
            raise ValueError(f"unknown operation {self.operation!r}")
        validate_segment("agency_id", self.agency_id)
        validate_segment("run_id", self.run_id)
        if self.dry_run and self.operation != "drain":
            raise ValueError("dry_run belongs to the drain")
        if self.repair and self.operation != "reconcile":
            raise ValueError("repair belongs to reconcile")
        if self.operation == "purge":
            if self.confirm != self.agency_id:
                raise ValueError(
                    "a purge must be confirmed with the agency id it deletes"
                )
        elif self.confirm is not None:
            raise ValueError("confirm belongs to the purge")


def operation_task(
    task: OperationTask, settings: Settings, work_dir: Path
) -> AnyReport:
    """One deletion operation. Raises only for a failure before the
    operation could report one — the A17 check, the wiring — and that
    failure has already been logged and recorded as the operation's own
    `failed` outcome, never the export's (A15 reads that one)."""
    state = OperationStateStore(work_dir, task.operation)
    started_at = utc_now_iso()
    try:
        # A17, authoritative, as for an export.
        require_single_agency(settings)
        context = build_deletion_context(
            settings, agency_id=task.agency_id, run_id=task.run_id
        )
    except Exception as exc:
        log_error_line(
            run_id=task.run_id,
            agency_id=task.agency_id,
            sink=settings.sink_id,
            error=exc,
        )
        report = _failed_before_start(task, settings.sink_id, started_at, exc)
        log_operation_outcome(task.operation, report)
        state.record(report)
        raise
    if task.operation == "drain":
        return DeletionDrain(
            sink=context.sink,
            manifests=context.manifests,
            work_dir=work_dir,
            state=state,
        ).drain(task.run_id, dry_run=task.dry_run)
    if task.operation == "reconcile":
        return Reconciler(
            sink=context.sink,
            manifests=context.manifests,
            work_dir=work_dir,
            state=state,
        ).reconcile(task.run_id, repair=task.repair)
    return Purger(
        sink=context.sink,
        manifests=context.manifests,
        chunker_fingerprint=context.chunker_fingerprint,
        work_dir=work_dir,
        state=state,
    ).purge(task.run_id, confirm=task.confirm or "")


def run_operation_process(
    task: OperationTask, settings: Settings, work_dir: Path
) -> None:
    """The `multiprocessing` target for /drain_deletions and
    /reconcile_async. Exits 1 on `failed`, 0 otherwise."""
    configure_logging()
    try:
        report = operation_task(task, settings, work_dir)
    except Exception:
        # Already reported. Exiting rather than re-raising keeps
        # multiprocessing from printing a traceback (F13).
        raise SystemExit(1) from None
    if report.outcome == "failed":
        raise SystemExit(1)


def _failed_before_start(
    task: OperationTask, sink_id: str, started_at: str, error: Exception
) -> AnyReport:
    common: dict[str, Any] = {
        "run_id": task.run_id,
        "agency_id": task.agency_id,
        "sink_id": sink_id,
        "outcome": "failed",
        "started_at": started_at,
        "finished_at": utc_now_iso(),
        "error_type": type(error).__name__,
    }
    if task.operation == "drain":
        return DrainReport(dry_run=task.dry_run, **common)
    if task.operation == "reconcile":
        return ReconcileReport(repair=task.repair, **common)
    return PurgeReport(**common)
