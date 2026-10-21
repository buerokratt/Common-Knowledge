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
"""

import logging
import secrets
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from exporter.api.config import Settings
from exporter.core.ids import validate_segment
from exporter.core.schemas import RunReport
from exporter.services.agency_guard import require_single_agency
from exporter.services.run_state import RunStateStore
from exporter.worker.utils import catch_error
from exporter.worker.wiring import (
    SourceFactory,
    build_coordinator,
    build_document_source,
)

__all__ = [
    "ExportTask",
    "configure_logging",
    "export_agency_task",
    "new_run_id",
    "run_export_process",
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
        sink=settings.content_sink,
        run_state=run_state,
    ):
        # A17, authoritative: a second agency would starve behind the
        # whole-run lock in silence, so it is a failed run instead.
        require_single_agency(settings)
        coordinator = build_coordinator(
            settings,
            agency_id=task.agency_id,
            work_dir=work_dir,
            run_state=run_state,
            source_factory=source_factory,
        )
    return coordinator.export(task.run_id, force=task.force, limit=task.limit)


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
