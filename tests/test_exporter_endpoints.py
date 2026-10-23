"""F9 — POST /export_agency_async and POST /drain_deletions.

`multiprocessing.Process` is replaced by a recorder: these tests assert what
the endpoint starts, not what the child does (tests/test_exporter_worker.py
covers that). Every test uses `with TestClient(app)` so the lifespan runs;
see tests/test_exporter_health.py for why that is load-bearing.
"""

import pickle
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from exporter.api.app import _PROCESS_CONTEXT, app
from exporter.api.config import Settings
from exporter.services.run_state import RunStateStore
from exporter.worker.tasks import ExportTask, run_export_process


class _RecordedProcess:
    """Stands in for multiprocessing.Process and records each start. A
    started one stays alive until the test says it finished."""

    started: list["_RecordedProcess"] = []

    def __init__(self, *, target: object, args: tuple[object, ...]) -> None:
        self.target = target
        self.args = args
        self.alive = False

    def start(self) -> None:
        self.alive = True
        _RecordedProcess.started.append(self)

    def is_alive(self) -> bool:
        return self.alive


@pytest.fixture
def processes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> list[_RecordedProcess]:
    """The health suite's environment, plus a recorded Process."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setattr(
        "exporter.api.app.check_single_agency_at_startup", lambda _settings: 1
    )
    monkeypatch.setenv("CONTENT_WORK_DIR", (tmp_path / "work").as_posix())
    monkeypatch.setattr(_PROCESS_CONTEXT, "Process", _RecordedProcess)
    # app is a module-level singleton, so a child one test started would
    # otherwise still be "running" in the next.
    monkeypatch.setattr(app.state, "export_process", None, raising=False)
    _RecordedProcess.started = []
    return _RecordedProcess.started


def test_an_export_starts_one_process_and_returns_at_once(
    processes: list[_RecordedProcess], tmp_path: Path
) -> None:
    with TestClient(app) as client:
        response = client.post(
            "/export_agency_async", json={"agency_base_id": "agency-1"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "started"
    assert body["agency_base_id"] == "agency-1"
    assert body["run_id"]
    assert len(processes) == 1
    process = processes[0]
    assert process.target is run_export_process
    task, settings, work_dir = process.args
    assert isinstance(task, ExportTask)
    assert task == ExportTask(agency_id="agency-1", run_id=body["run_id"])
    assert isinstance(settings, Settings)
    # The RESOLVED work dir, where A14 checked the lock works.
    assert work_dir == (tmp_path / "work").resolve()


def test_a_supplied_run_id_and_force_are_passed_through(
    processes: list[_RecordedProcess],
) -> None:
    with TestClient(app) as client:
        response = client.post(
            "/export_agency_async",
            json={"agency_base_id": "agency-1", "run_id": "cron-17", "force": True},
        )

    assert response.json()["run_id"] == "cron-17"
    task = processes[0].args[0]
    assert task == ExportTask(agency_id="agency-1", run_id="cron-17", force=True)


def test_the_http_body_cannot_accept_existing_content(
    processes: list[_RecordedProcess],
) -> None:
    """F21's override is an operator's deliberate act at the CLI. The cron
    path goes through this endpoint, so the field is ignored here."""
    with TestClient(app) as client:
        client.post(
            "/export_agency_async",
            json={"agency_base_id": "agency-1", "accept_existing_content": True},
        )

    task = processes[0].args[0]
    assert isinstance(task, ExportTask)
    assert task.accept_existing_content is False


@pytest.mark.parametrize(
    "body",
    [
        {"agency_base_id": "agency-1", "run_id": "../x"},
        {"agency_base_id": "agency-1", "run_id": "a/b"},
        {"agency_base_id": "a/b"},
        {"agency_base_id": ""},
        {},
    ],
)
def test_an_id_that_would_escape_its_folder_is_refused(
    processes: list[_RecordedProcess], body: dict[str, object]
) -> None:
    """Both ids become path segments; refusing here means no process ever
    starts with one."""
    with TestClient(app) as client:
        response = client.post("/export_agency_async", json=body)

    assert response.status_code == 422
    assert processes == []


def test_a_second_export_while_the_first_runs_starts_nothing_and_is_busy(
    processes: list[_RecordedProcess], tmp_path: Path
) -> None:
    """A cron misfire or retry storm must not pile up children that each
    make the agency check before meeting the lock. The busy is recorded, as
    the child's would have been (G9 counts them)."""
    with TestClient(app) as client:
        first = client.post("/export_agency_async", json={"agency_base_id": "a-1"})
        second = client.post(
            "/export_agency_async", json={"agency_base_id": "a-1", "run_id": "r-2"}
        )

    assert first.json()["status"] == "started"
    assert second.status_code == 200
    assert second.json() == {"status": "busy", "agency_base_id": "a-1", "run_id": "r-2"}
    assert len(processes) == 1
    last = RunStateStore(tmp_path / "work").read()
    assert last is not None
    assert (last.outcome, last.run_id, last.consecutive_busy) == ("busy", "r-2", 1)


def test_a_finished_child_does_not_block_the_next_export(
    processes: list[_RecordedProcess],
) -> None:
    with TestClient(app) as client:
        client.post("/export_agency_async", json={"agency_base_id": "a-1"})
        processes[0].alive = False
        response = client.post("/export_agency_async", json={"agency_base_id": "a-1"})

    assert response.json()["status"] == "started"
    assert len(processes) == 2


def test_the_child_is_spawned_not_forked() -> None:
    """Forking from a threadpool thread can copy a held lock into the
    child; see exporter/api/app.py."""
    assert _PROCESS_CONTEXT.get_start_method() == "spawn"


def test_what_the_child_is_given_survives_pickling(
    processes: list[_RecordedProcess],
) -> None:
    """Spawn pickles the target's arguments; one that does not pickle fails
    in the child, after the endpoint has already answered `started`."""
    with TestClient(app) as client:
        client.post("/export_agency_async", json={"agency_base_id": "a-1"})

    process = processes[0]
    target, args = pickle.loads(pickle.dumps((process.target, process.args)))
    assert target is run_export_process
    assert args == process.args


def test_drain_deletions_is_not_built_yet(processes: list[_RecordedProcess]) -> None:
    with TestClient(app) as client:
        response = client.post("/drain_deletions")

    assert response.status_code == 501
    assert "J1" in response.json()["detail"]
    assert processes == []


def test_health_shows_the_error_type_of_a_failed_run(
    processes: list[_RecordedProcess], tmp_path: Path
) -> None:
    """F12: a failure the export child recorded is visible here, by type."""
    RunStateStore(tmp_path / "work").record(
        outcome="failed",
        run_id="r-1",
        agency_id="agency-1",
        duration_seconds=0.5,
        error_type="ConfigurationError",
    )
    with TestClient(app) as client:
        last_run = client.get("/health").json()["last_run"]

    assert last_run["outcome"] == "failed"
    assert last_run["error_type"] == "ConfigurationError"
