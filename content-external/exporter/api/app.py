"""FastAPI surface for the content-external exporter.

Settings are built once in the lifespan and stashed on app.state. Handlers
receive them via Depends; services built by later stages receive them as
constructor arguments. Nothing imports a settings object from
exporter.api.config — there isn't one.

Later stages add POST /export_agency_async and POST /drain_deletions here.
"""

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Annotated

from fastapi import Depends, FastAPI, Request

from exporter.api.config import (
    ConfigurationError,
    Settings,
    assert_memory_budget,
    assert_work_dir_usable,
    load_settings,
    log_redacted_config,
)
from exporter.api.models import HealthResponse, LastRunSummary
from exporter.services.agency_guard import (
    MultipleAgenciesError,
    check_single_agency_at_startup,
)
from exporter.services.run_state import RunStateStore

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Fail fast, then serve.

    If anything here raises, uvicorn logs "Application startup failed" and
    exits non-zero — compose shows Exited, Kubernetes shows CrashLoopBackOff.
    That is deliberate and not a 503 /health: with a probe, a 503-forever pod
    still sits Running and 1/1, green in `kubectl get pods`, doing nothing.
    That is exactly the silent-permanent-failure mode A15 exists to kill.
    """
    # Must come first. uvicorn attaches no handler to the ROOT logger, so
    # without this the INFO config echo below falls through to
    # logging.lastResort, which only emits at WARNING — the echo would be
    # silently dropped and this would ship looking correct.
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s - %(asctime)s - %(name)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    # Order matters: cheapest and most certain first, so a misconfiguration is
    # reported by the check that can name it rather than by a later one that
    # merely trips over it.
    settings = load_settings()  # A12's sink/manifest-store matrix
    log_redacted_config(settings)
    work_dir = assert_work_dir_usable(settings)  # A14's exclusive-flock self-test
    assert_memory_budget(settings)  # A16

    # A17, last: the only check here that makes a network call, and the only
    # one that tolerates its own failure. MultipleAgenciesError is translated
    # rather than raised directly, so every startup refusal this service can
    # produce is a ConfigurationError.
    try:
        check_single_agency_at_startup(settings)
    except MultipleAgenciesError as exc:
        raise ConfigurationError(str(exc)) from exc

    app.state.settings = settings
    # Built on the RESOLVED path, not settings.content_work_dir: A14 already
    # followed the symlinks and re-applied rule 1 to the real directory, and
    # the last-run record must land in the same place a forked export child or
    # the run_sync CLI will write it.
    app.state.run_state = RunStateStore(work_dir)
    yield


app = FastAPI(title="Content External Exporter", version="0.1.0", lifespan=lifespan)


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_run_state(request: Request) -> RunStateStore:
    store: RunStateStore = request.app.state.run_state
    return store


@app.get("/health")
def health(
    settings: Annotated[Settings, Depends(get_settings)],
    run_state: Annotated[RunStateStore, Depends(get_run_state)],
) -> HealthResponse:
    """Liveness, configuration identity, and the last run's outcome.

    Always 200 while the process is serving, and a failed last run
    deliberately does NOT become a non-200. A liveness probe would then
    restart the pod, which fixes neither a wrong base URL nor a rotated
    credential, and the resulting CrashLoopBackOff would hide the one log line
    that says why. `last_run.outcome` is where a failure is visible; the
    status code is about whether the process is serving.

    Sync `def`, not `async def`, matching cleaning/api/app.py: FastAPI runs
    sync handlers in a threadpool, so the long-running export handler Stage F
    adds cannot starve the probe.
    """
    last_run = run_state.read()
    return HealthResponse(
        status="ok",
        sink=settings.content_sink,
        store_backend=settings.content_external_store_backend,
        chunk_profile=settings.chunk_profile,
        work_dir=settings.content_work_dir,
        last_run=LastRunSummary(**asdict(last_run)) if last_run else None,
    )
