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
from typing import Annotated

from fastapi import Depends, FastAPI, Request

from exporter.api.config import (
    Settings,
    assert_memory_budget,
    assert_work_dir_usable,
    load_settings,
    log_redacted_config,
)
from exporter.api.models import HealthResponse

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
    assert_work_dir_usable(settings)  # A14's exclusive-flock self-test
    assert_memory_budget(settings)  # A16

    app.state.settings = settings
    yield


app = FastAPI(title="Content External Exporter", version="0.1.0", lifespan=lifespan)


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


@app.get("/health")
def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    """Liveness and configuration identity.

    Always 200 while the process is serving. A15 fills in last_run, and a
    failed last run must NOT become a non-200: the liveness probe would then
    restart the pod, which does not fix a wrong base URL or a rotated
    credential, and the resulting CrashLoopBackOff hides the one log line that
    says why.

    Sync `def`, not `async def`, matching cleaning/api/app.py: FastAPI runs
    sync handlers in a threadpool, so the long-running export handler Stage F
    adds cannot starve the probe.
    """
    return HealthResponse(
        status="ok",
        sink=settings.content_sink,
        store_backend=settings.content_external_store_backend,
        chunk_profile=settings.chunk_profile,
        work_dir=settings.content_work_dir,
    )
