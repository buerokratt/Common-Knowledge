"""Pydantic models for the API boundary only.

Plain dataclasses are the house style everywhere under core/ (B2); pydantic
stays at the edge, which is here.

Contract rule for /health: ADDITIVE ONLY. Every field A15 or a later stage
adds must be optional with a default, so an alert rule or a probe written
against today's response keeps working. Nothing here is ever removed or
retyped.
"""

from typing import Literal

from pydantic import BaseModel


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
