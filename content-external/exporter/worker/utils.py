"""Error reporting for an export that failed before it could report (F12).

THE SHAPE IS CLEANING'S, THE DESTINATION IS NOT. cleaning/worker/utils.py's
`catch_error` catches, logs, calls `send_error` and re-raises; this does the
same. But cleaning's `send_error` POSTs to Ruuter's `reports/logs/add`, which
writes a `source_run_page` row keyed to a `source_run_report_base_id` — a
scrape's monitoring record, which an export does not have — and it would make
this service write to CKB, whose read-only posture is a cross-cutting rule. So
`send_error` here reports to the two surfaces A15 already built:

- one ERROR line behind the stable prefix `content-external error`, carrying
  the run's identity, the exception type and its sanitised message;
- the A15 terminal line (`content-external run ... outcome=failed`) and the
  last-run record, so `/health` and the agreed alert rule (two consecutive
  `failed`) see a failure that happened before the coordinator could report
  one: a configuration refusal, a factory error, the A17 agency check.

A failure INSIDE an export never reaches this: PublishCoordinator.export()
turns every Exception into a `failed` report and records it itself, so the
same failure is never recorded twice.

REDACTION (F13). Type and sanitised, whitespace-collapsed, length-capped
message only. Never a traceback — its frames could hold a document — and the
last-run record, which /health serves unauthenticated, gets the type alone.
"""

import contextlib
import logging
import time
from collections.abc import Iterator

from exporter.core.redaction import sanitize_sensitive_text
from exporter.services.run_log import log_run_outcome, scrub_value
from exporter.services.run_state import RunStateStore

__all__ = ["ERROR_LOG_PREFIX", "catch_error", "send_error"]

logger = logging.getLogger(__name__)

# Grep this beside `content-external run` and `content-external config`.
ERROR_LOG_PREFIX = "content-external error"

# Long enough for a Resql URL path or a store key and an error code; short
# enough that a library message quoting more than that is cut off.
_MAX_MESSAGE_CHARS = 300


def send_error(
    *,
    run_id: str,
    agency_id: str,
    sink: str,
    error: BaseException,
    run_state: RunStateStore | None,
    duration_seconds: float,
) -> None:
    """Report `error` as this run's `failed` outcome. Never raises: losing
    the report must not replace the error being reported."""
    error_type = type(error).__name__
    # Last on the line, so it keeps its spaces: everything after `message=`
    # is the message. Newlines and runs of whitespace still collapse, so one
    # error is one line.
    message = " ".join(sanitize_sensitive_text(str(error)).split())
    message = message[:_MAX_MESSAGE_CHARS] or "-"
    try:
        logger.error(
            "%s run_id=%s agency_id=%s sink=%s error_type=%s message=%s",
            ERROR_LOG_PREFIX,
            scrub_value(run_id),
            scrub_value(agency_id),
            scrub_value(sink),
            scrub_value(error_type),
            message,
        )
        log_run_outcome(
            outcome="failed",
            run_id=run_id,
            agency_id=agency_id,
            sink=sink,
            duration_seconds=duration_seconds,
            error_type=error_type,
        )
        if run_state is not None:
            run_state.record(
                outcome="failed",
                run_id=run_id,
                agency_id=agency_id,
                duration_seconds=duration_seconds,
                error_type=error_type,
            )
    except Exception as exc:
        logger.warning(
            "%s could not be fully recorded: %s", ERROR_LOG_PREFIX, type(exc).__name__
        )


@contextlib.contextmanager
def catch_error(
    *,
    run_id: str,
    agency_id: str,
    sink: str,
    run_state: RunStateStore | None,
) -> Iterator[None]:
    """Report any Exception from the block with `send_error`, then re-raise
    it, as cleaning's `catch_error` does. The caller decides what a raise
    means for its process; it must not print a traceback (F13)."""
    started = time.perf_counter()
    try:
        yield
    except Exception as exc:
        send_error(
            run_id=run_id,
            agency_id=agency_id,
            sink=sink,
            error=exc,
            run_state=run_state,
            duration_seconds=time.perf_counter() - started,
        )
        raise
