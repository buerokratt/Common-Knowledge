"""FastAPI surface for the content-external exporter.

Settings are built once in the lifespan and stashed on app.state. Handlers
receive them via Depends; services built by later stages receive them as
constructor arguments. Nothing imports a settings object from
exporter.api.config — there isn't one.

POST /export_agency_async is the production surface (F9): G1's cron step
calls it, and it starts the export in its own process and returns at once,
as cleaning/api/app.py's /clean_source_async does — but with a spawned
process and at most one live export child; see the handler. The CLI
(python -m exporter.run_sync) is an operations tool beside it. POST
/drain_deletions is Stage J's (J1) and answers 501 until then, so the route
G1 and J3 will call already exists.
"""

import multiprocessing
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status

from exporter.api.config import (
    ConfigurationError,
    Settings,
    assert_memory_budget,
    assert_work_dir_usable,
    load_settings,
    log_redacted_config,
)
from exporter.api.models import (
    ExportRequest,
    ExportStarted,
    HealthResponse,
    LastRunSummary,
)
from exporter.services.agency_guard import (
    MultipleAgenciesError,
    check_single_agency_at_startup,
)
from exporter.services.run_log import log_run_outcome
from exporter.services.run_state import RunStateStore
from exporter.worker.tasks import (
    ExportTask,
    configure_logging,
    new_run_id,
    run_export_process,
)


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
    configure_logging()

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
    # the last-run record must land in the same place a spawned export child or
    # the run_sync CLI will write it.
    app.state.run_state = RunStateStore(work_dir)
    # The export child gets the same resolved path, for the same reason.
    app.state.work_dir = work_dir
    yield


app = FastAPI(title="Content External Exporter", version="0.1.0", lifespan=lifespan)

# Spawn, not the Linux default fork. The endpoint is a sync handler, so it
# runs on a threadpool thread, and forking a multithreaded process copies
# whatever locks the other threads hold at that instant — a logging handler's,
# say — into a child with no thread left to release them. Spawn starts a
# clean interpreter; ExportTask, Settings and Path all pickle.
_PROCESS_CONTEXT = multiprocessing.get_context("spawn")

# Sync handlers run concurrently on the threadpool; this makes the endpoint's
# check-then-start one step, so two requests cannot both find no live child.
_START_LOCK = threading.Lock()


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_run_state(request: Request) -> RunStateStore:
    store: RunStateStore = request.app.state.run_state
    return store


def get_work_dir(request: Request) -> Path:
    work_dir: Path = request.app.state.work_dir
    return work_dir


@app.post("/export_agency_async")
def export_agency_async(
    request: Request,
    body: ExportRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    work_dir: Annotated[Path, Depends(get_work_dir)],
    run_state: Annotated[RunStateStore, Depends(get_run_state)],
) -> ExportStarted:
    """Start one export in its own process and return immediately (F9).

    The run lock, taken by the child, stays the only authority on `busy`.
    What this adds is a bound: while the export child this process last
    started is still alive, a new request starts nothing and records `busy`
    here. Without it a cron misfire or a retry storm piles up children, each
    making the run-start agency check (with backoff) before finding the lock
    held. It is not a second answer to "may this run": it never touches the
    lock, so it cannot take it from a child, and a child started after a
    check that said "free" still meets the lock.
    """
    task = ExportTask(
        agency_id=body.agency_base_id,
        run_id=body.run_id or new_run_id(),
        force=body.force,
    )
    with _START_LOCK:
        return _start_export(request, task, settings, work_dir, run_state)


def _start_export(
    request: Request,
    task: ExportTask,
    settings: Settings,
    work_dir: Path,
    run_state: RunStateStore,
) -> ExportStarted:
    # Reaps finished children, which would otherwise linger as zombies
    # between hourly ticks.
    multiprocessing.active_children()
    running: BaseProcess | None = getattr(request.app.state, "export_process", None)
    if running is not None and running.is_alive():
        log_run_outcome(
            outcome="busy",
            run_id=task.run_id,
            agency_id=task.agency_id,
            sink=settings.content_sink,
            duration_seconds=0.0,
        )
        run_state.record(
            outcome="busy",
            run_id=task.run_id,
            agency_id=task.agency_id,
            duration_seconds=0.0,
        )
        return ExportStarted(
            status="busy", agency_base_id=task.agency_id, run_id=task.run_id
        )

    process = _PROCESS_CONTEXT.Process(
        target=run_export_process, args=(task, settings, work_dir)
    )
    process.start()
    request.app.state.export_process = process
    return ExportStarted(
        status="started", agency_base_id=task.agency_id, run_id=task.run_id
    )


@app.post("/drain_deletions")
def drain_deletions() -> None:
    """Stage J (J1). The route exists so its caller's contract is stable;
    until the drain is built it refuses rather than reporting a drain that
    did not happen."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="drain_deletions is Stage J (J1); not built yet",
    )


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
