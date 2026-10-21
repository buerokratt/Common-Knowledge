"""Pydantic models for the API boundary only.

Plain dataclasses are the house style everywhere under core/ (B2); pydantic
stays at the edge, which is here.

Contract rule for /health: ADDITIVE ONLY. Every field A15 or a later stage
adds must be optional with a default, so an alert rule or a probe written
against today's response keeps working. Nothing here is ever removed or
retyped.
"""

from typing import Literal

from pydantic import BaseModel, field_validator

from exporter.core.ids import validate_segment


class LastRunSummary(BaseModel):
    """The last terminal run, as recorded on CONTENT_WORK_DIR.

    None until a run completes, which is honest rather than optimistic — an
    hourly job that has never run is not the same as one that ran and
    succeeded.

    The five original fields are unchanged from the shape declared before A15,
    which is what the contract rule above bought. A15 added the two counters,
    both optional with defaults.

    The source of truth is exporter/services/run_state.py's LastRun, a plain
    dataclass; this is its rendering at the API boundary. The outcome Literal
    is duplicated between the two on purpose — pydantic belongs at the edge
    and services/ must not import api/ — and a test asserts they agree.
    """

    outcome: Literal["success", "unchanged", "busy", "failed"]
    run_id: str
    agency_id: str
    finished_at: str
    duration_seconds: float
    # A15. Here rather than computed by a monitoring surface because the
    # agreed alert threshold is "two consecutive failed, or no success in 6
    # hours" — one failure is the design working — and because G9 requires
    # consecutive `busy` outcomes to be counted rather than discarded. A run
    # that reports `busy` every tick means the export is taking longer than
    # the cron interval, at which point detection latency is no longer bounded
    # by that interval and genuine lock contention is indistinguishable from
    # normal operation.
    consecutive_failures: int = 0
    consecutive_busy: int = 0
    # F12. The exception class name behind a `failed` outcome, never its
    # message: this endpoint is unauthenticated.
    error_type: str | None = None


class HealthResponse(BaseModel):
    """Liveness plus configuration identity.

    Deliberately excluded: external_s3_endpoint_url, any bucket or container
    name, llm_module_base_url, anything from Vault. /health is unauthenticated
    on bykstack. The fields below are the ones content-external/README.md
    already promises an operator ("Check CONTENT_SINK ... or GET /health"),
    and none of them identifies a destination.
    """

    status: Literal["ok"]
    sink: str
    store_backend: str
    chunk_profile: str
    work_dir: str
    last_run: LastRunSummary | None = None


class ExportRequest(BaseModel):
    """POST /export_agency_async's body, as G1's Ruuter step sends it.

    Both ids become path segments — the agency id in every key, the run id in
    the deletion journal's filename — so a value that would escape its folder
    ("../x", "a/b") is a 422 here, before any process starts.
    """

    agency_base_id: str
    # Generated when absent: the cron trigger need not invent one.
    run_id: str | None = None
    # Bypasses Gate 1 once E4 builds it; echoed in the report until then.
    force: bool = False

    @field_validator("agency_base_id")
    @classmethod
    def _check_agency(cls, value: str) -> str:
        return validate_segment("agency_base_id", value)

    @field_validator("run_id")
    @classmethod
    def _check_run_id(cls, value: str | None) -> str | None:
        return None if value is None else validate_segment("run_id", value)


class ExportStarted(BaseModel):
    """`started`: the export is running in its own process. Its outcome is
    not here: it lands in /health's last_run and the `content-external run`
    log line, and a started export can still turn out `busy` at the run lock.

    `busy`: the export this service started last is still running, so
    nothing was started and `busy` was recorded as that run's outcome. The
    caller treats both the same way; the next tick is the retry."""

    status: Literal["started", "busy"]
    agency_base_id: str
    run_id: str
