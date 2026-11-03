"""What the deletion operations share (Stage J): their reports, the one
greppable line each terminal outcome emits, the last-outcome record /health
serves, and the run-lock discipline every one of them follows.

THE OPERATIONS. `drain` (J1, J2) retries the deletes earlier runs journalled
and the destination did not confirm. `reconcile` (J4, J5, J9) compares the
destination with the committed manifest and, when asked, removes orphaned
tail chunks. `purge` (J7, J8) deletes everything the manifest records for an
agency, on an operator's explicit request.

ONE LOCK, SAME AS THE EXPORT (F2, flagged risk 8). Every operation takes the
whole-run lock before it reads anything, so none of them can delete a key an
export is re-publishing. Held: the operation reports `busy`, touches nothing
and is not an error — exactly as an export does.

NOT THE EXPORT'S RECORD. An operation's outcome lands in `last_<operation>.json`
beside `last_run.json`, never in it: A15's alert ("two consecutive failed, or
no success in 6 hours") is about the export, and a daily reconcile or a dry
drain must neither reset nor feed its streaks.

REDACTION (F13). Reports carry ids, counts and short reason codes. The log
line carries only the scalar fields — counts, flags, outcome — so a document
id appears in the JSON report an operator asked for, never in a log stream.
"""

import json
import logging
import os
import tempfile
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Literal

from exporter.core.redaction import sanitize_sensitive_text
from exporter.core.schemas import to_json_dict
from exporter.services.locking import run_lock
from exporter.services.run_log import scrub_value

__all__ = [
    "MAX_REPORTED_IDS",
    "OPERATION_LOG_PREFIX",
    "AnyReport",
    "DrainReport",
    "Operation",
    "OperationStateStore",
    "PurgeReport",
    "ReconcileReport",
    "format_operation_line",
    "log_operation_outcome",
    "report_json",
    "run_operation",
]

logger = logging.getLogger(__name__)

Operation = Literal["drain", "reconcile", "purge"]

# Grep `content-external drain`, `content-external reconcile` or
# `content-external purge`, beside `content-external run` (A15).
OPERATION_LOG_PREFIX = "content-external"

# How many ids of one kind a report lists. The counts are always whole; the
# lists are for an operator to start from, not an inventory.
MAX_REPORTED_IDS = 50

# Report fields the log line never carries: timestamps make every line
# unique for no gain, and the id lists stay in the JSON report (F13).
_UNLOGGED = frozenset({"started_at", "finished_at"})


@dataclass(frozen=True)
class DrainReport:
    """One drain (J1). `dry_run` drains nothing: it reports what it would."""

    run_id: str
    agency_id: str
    sink_id: str
    # drained / nothing_pending / dry_run / busy / failed
    outcome: str
    started_at: str
    finished_at: str | None
    dry_run: bool
    duration_seconds: float = 0.0
    error_type: str | None = None
    # Closed journals read, and journals no committed run has closed yet —
    # the next export's to reconcile, never the drain's.
    journals_scanned: int = 0
    awaiting_export: int = 0
    # Pending whole-document deletes, after the manifest re-check.
    candidates: int = 0
    # Pending documents the committed manifest lists again: restored and
    # re-published since. Never deleted.
    skipped_republished: int = 0
    deleted: int = 0
    missing: int = 0
    still_unresolved: int = 0
    # Documents with an unconfirmed tail delete. The next export retries
    # those itself (diff.record() keeps their previous count), so the drain
    # only counts them.
    tails_left_to_export: int = 0
    # Documents with an unconfirmed orphan-tail delete from reconcile's
    # repair (J5). The export never revisits ordinals the manifest did not
    # count; the next `reconcile --repair` finds them in its listing again.
    tails_left_to_reconcile: int = 0
    # The candidates by id, up to MAX_REPORTED_IDS. JSON report only.
    candidate_ids: tuple[str, ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class ReconcileReport:
    """One reconcile (J4). `scope` is how much of the destination could be
    seen: `full` (a listing), `manifest_documents` (read-backs of what the
    manifest lists; anything else at the destination is invisible) or
    `none` — the sink can do neither, and the outcome is `unverifiable`,
    never `clean` (J9). `clean` is only ever a full-scope result: no drift
    at `manifest_documents` is `clean_partial`, because content the
    manifest does not list was never looked at."""

    run_id: str
    agency_id: str
    sink_id: str
    # clean / clean_partial / drift / repaired / unverifiable / busy / failed
    outcome: str
    started_at: str
    finished_at: str | None
    repair: bool
    scope: str = "none"
    duration_seconds: float = 0.0
    error_type: str | None = None
    # What the manifest claims.
    manifest_documents: int = 0
    manifest_chunks: int = 0
    # What the destination was seen to hold (full scope; read-backs count
    # only what they probed).
    destination_documents: int = 0
    # Drift, one count per category.
    missing_documents: int = 0
    record_missing: int = 0
    chunk_gaps: int = 0
    orphan_tail_documents: int = 0
    orphan_tail_chunks: int = 0
    unrecorded_documents: int = 0
    # J5's repair, when asked for and the scope is full.
    tails_repaired: int = 0
    tails_unresolved: int = 0
    # category -> up to MAX_REPORTED_IDS document ids. JSON report only.
    drift_ids: Mapping[str, tuple[str, ...]] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class PurgeReport:
    """One purge (J7). `residual` is whether the destination was confirmed
    empty afterwards: `verified_empty`, `not_empty`, or `unverifiable` on a
    sink that cannot list (J9) — requested versus acknowledged is then all
    there is to report, and the outcome says so: `purged` only when the
    destination was confirmed empty, `purged_unverified` when every requested
    delete was acknowledged but residual state could not be checked."""

    run_id: str
    agency_id: str
    sink_id: str
    # purged / purged_unverified / incomplete / nothing_to_purge / busy / failed
    outcome: str
    started_at: str
    finished_at: str | None
    duration_seconds: float = 0.0
    error_type: str | None = None
    requested: int = 0
    deleted: int = 0
    missing: int = 0
    unresolved: int = 0
    deletions_recorded: int = 0
    # The agency-scoped delete for anything the manifest never recorded:
    # deleted / missing / failed / deferred / not_supported / not_run.
    residue: str = "not_run"
    residual: str = "unverifiable"
    # The documents left unresolved, up to MAX_REPORTED_IDS. JSON report only.
    unresolved_ids: tuple[str, ...] = field(default=(), repr=False)


AnyReport = DrainReport | ReconcileReport | PurgeReport


def _scalar_fields(report: AnyReport) -> dict[str, object]:
    """The report's loggable fields: scalars only, timestamps excluded."""
    out: dict[str, object] = {}
    for spec in fields(report):
        if spec.name in _UNLOGGED:
            continue
        value = getattr(report, spec.name)
        if value is None or isinstance(value, (str, int, float, bool)):
            out[spec.name] = value
    return out


def _render(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.3f}"
    return scrub_value(value)


def format_operation_line(operation: Operation, report: AnyReport) -> str:
    """`content-external <operation> k=v ...`, keys sorted, every scalar
    field present — a field that appears only sometimes is one no alert rule
    can rely on. Pure, so it is testable."""
    values = _scalar_fields(report)
    body = " ".join(f"{key}={_render(values[key])}" for key in sorted(values))
    return f"{OPERATION_LOG_PREFIX} {operation} {body}"


def log_operation_outcome(operation: Operation, report: AnyReport) -> str:
    """Emit the line once and return it. `failed` at ERROR, everything else
    — `busy` included — at INFO."""
    line = format_operation_line(operation, report)
    if report.outcome == "failed":
        logger.error(line)
    else:
        logger.info(line)
    return line


class OperationStateStore:
    """The last terminal outcome of one operation, as `last_<operation>.json`
    under CONTENT_WORK_DIR. One record, overwritten; no streaks (see the
    module docstring). A plain write-replace: there is no read-modify-write
    to serialise."""

    def __init__(self, work_dir: Path, operation: Operation) -> None:
        self._operation = operation
        self._path = work_dir / f"last_{operation}.json"

    @property
    def path(self) -> Path:
        return self._path

    def record(self, report: AnyReport) -> None:
        """Persist the report's scalar fields plus its finish time. Never
        raises: losing the record of an operation must not change its
        outcome."""
        summary = _scalar_fields(report)
        summary["finished_at"] = report.finished_at
        tmp: str | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                prefix=f"{self._path.name}.", suffix=".tmp", dir=self._path.parent
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(summary, sort_keys=True, ensure_ascii=False, indent=2)
                )
            os.replace(tmp, self._path)
        except OSError:
            logger.warning(
                "could not write the last-%s record to %s", self._operation, self._path
            )
            if tmp is not None:
                with suppress(OSError):
                    Path(tmp).unlink(missing_ok=True)

    def read(self) -> dict[str, Any] | None:
        """The last record, or None: never run, missing, or unreadable.
        Never raises — /health must not fail over its own bookkeeping."""
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict):
            return None
        return {
            str(key): value
            for key, value in raw.items()
            if value is None or isinstance(value, (str, int, float, bool))
        }


def run_operation[R: (DrainReport, ReconcileReport, PurgeReport)](
    operation: Operation,
    *,
    work_dir: Path,
    run_id: str,
    body: Callable[[], R],
    on_busy: Callable[[], R],
    on_error: Callable[[Exception], R],
    state: OperationStateStore | None,
) -> R:
    """Run `body` under the whole-run lock (F2) and record what it came to.

    Held: `on_busy()`, having touched nothing. Any Exception from `body`, or
    a lock file that cannot be opened: `on_error(exc)`, logged by type and
    sanitised message, never a traceback (F13). Every outcome is stamped
    with its duration, logged once and recorded once."""
    started = time.perf_counter()
    report: R | None = None
    try:
        with run_lock(work_dir) as acquired:
            if acquired:
                try:
                    report = body()
                except Exception as exc:
                    _log_failure(operation, run_id, exc)
                    report = on_error(exc)
                # Recorded while the lock is held, as an export does.
                report = _finish(operation, report, started, state)
                return report
    except OSError as exc:
        if report is not None:
            # Releasing failed after the operation finished and was recorded:
            # return the recorded report, duration included.
            return report
        _log_failure(operation, run_id, exc)
        return _finish(operation, on_error(exc), started, state)
    logger.info(
        "%s %s: another export or operation holds the run lock", operation, run_id
    )
    return _finish(operation, on_busy(), started, state)


def _log_failure(operation: Operation, run_id: str, exc: Exception) -> None:
    logger.error(
        "%s %s failed: %s: %s",
        operation,
        run_id,
        type(exc).__name__,
        sanitize_sensitive_text(str(exc)),
    )


def _finish[R: (DrainReport, ReconcileReport, PurgeReport)](
    operation: Operation,
    report: R,
    started: float,
    state: OperationStateStore | None,
) -> R:
    report = replace(report, duration_seconds=time.perf_counter() - started)
    try:
        log_operation_outcome(operation, report)
        if state is not None:
            state.record(report)
    except Exception as exc:
        logger.warning(
            "%s %s: its outcome could not be recorded: %s",
            operation,
            report.run_id,
            type(exc).__name__,
        )
    return report


def report_json(report: AnyReport) -> object:
    """The whole report — id lists included — ready for json.dump."""
    return to_json_dict(report)
