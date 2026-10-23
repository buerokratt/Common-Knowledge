"""F12 and the export task — catch_error / send_error, the composition root,
and the multiprocessing target.

No network, no Docker: the coordinator runs over the in-memory fakes, and
A17's agency check is stubbed.
"""

import logging
from pathlib import Path

import pytest

from exporter.api.config import ConfigurationError, Settings
from exporter.core.schemas import RunReport
from exporter.services.document_source import CkbDocumentSource
from exporter.services.run_log import RUN_LOG_PREFIX
from exporter.services.run_state import RunStateStore
from exporter.worker import tasks, wiring
from exporter.worker.tasks import ExportTask, export_agency_task, run_export_process
from exporter.worker.utils import ERROR_LOG_PREFIX, catch_error, send_error
from exporter_fakes import (
    FakeDocumentSource,
    InMemoryStore,
    RecordingSink,
    make_row,
    patch_wiring,
)

AGENCY = "agency-1"
TEXT = "Lause räägib sellest, kuidas taotlus esitada. " * 5


def _settings(work_dir: Path) -> Settings:
    return Settings(content_work_dir=work_dir.as_posix())  # pyright: ignore[reportCallIssue]


@pytest.fixture
def work_dir(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


# --- catch_error / send_error -------------------------------------------------


def test_catch_error_records_a_failed_run_and_reraises(
    work_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = RunStateStore(work_dir)
    with (
        caplog.at_level(logging.INFO),
        pytest.raises(ValueError, match="boom"),
        catch_error(
            run_id="r-1", agency_id=AGENCY, sink="object_store", run_state=store
        ),
    ):
        raise ValueError("boom")

    last = store.read()
    assert last is not None
    assert last.outcome == "failed"
    assert last.error_type == "ValueError"
    lines = [r.getMessage() for r in caplog.records]
    error_lines = [line for line in lines if line.startswith(ERROR_LOG_PREFIX)]
    run_lines = [line for line in lines if line.startswith(RUN_LOG_PREFIX)]
    assert len(error_lines) == 1
    assert "error_type=ValueError" in error_lines[0]
    assert "message=boom" in error_lines[0]
    assert len(run_lines) == 1
    assert "outcome=failed" in run_lines[0]
    assert "error_type=ValueError" in run_lines[0]


def test_send_error_redacts_and_never_logs_a_traceback(
    work_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    error = RuntimeError("could not reach https://svc:hunter2@resql.example/x")
    with caplog.at_level(logging.INFO):
        send_error(
            run_id="r-1",
            agency_id=AGENCY,
            sink="object_store",
            error=error,
            run_state=RunStateStore(work_dir),
            duration_seconds=0.1,
        )
    assert "hunter2" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_send_error_caps_the_message(
    work_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR):
        send_error(
            run_id="r-1",
            agency_id=AGENCY,
            sink="object_store",
            error=ValueError("x" * 5000),
            run_state=None,
            duration_seconds=0.1,
        )
    line = next(
        r.getMessage() for r in caplog.records if ERROR_LOG_PREFIX in r.getMessage()
    )
    assert len(line) < 600


def test_send_error_never_raises(
    work_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Losing the report must not replace the error being reported."""

    def broken(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    store = RunStateStore(work_dir)
    monkeypatch.setattr(store, "record", broken)
    send_error(
        run_id="r-1",
        agency_id=AGENCY,
        sink="object_store",
        error=ValueError("boom"),
        run_state=store,
        duration_seconds=0.1,
    )


# --- export_agency_task -------------------------------------------------------


def test_the_real_wiring_builds_stage_e_document_source(work_dir: Path) -> None:
    """Building it reaches neither Resql nor S3: the client is lazy."""
    source = wiring.build_document_source(_settings(work_dir))
    assert isinstance(source, CkbDocumentSource)


def test_the_real_wiring_gets_past_the_source_to_the_destination(
    work_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unpatched apart from A17: the source builds, so the first refusal is
    the unconfigured destination's, as a failed run."""
    monkeypatch.setattr(tasks, "require_single_agency", lambda _s: 1)
    task = ExportTask(agency_id=AGENCY, run_id="r-1")

    with pytest.raises(ConfigurationError, match="EXTERNAL_S3_ENDPOINT_URL"):
        export_agency_task(task, _settings(work_dir), work_dir)

    last = RunStateStore(work_dir).read()
    assert last is not None
    assert last.outcome == "failed"
    assert last.error_type == "ConfigurationError"


def test_a_second_agency_fails_the_run_before_anything_is_built(
    work_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from exporter.services.agency_guard import MultipleAgenciesError

    def two(_settings: Settings) -> int:
        raise MultipleAgenciesError("2 agencies exist")

    built: list[object] = []
    monkeypatch.setattr(tasks, "require_single_agency", two)
    monkeypatch.setattr(tasks, "build_coordinator", lambda *a, **k: built.append(a))

    with pytest.raises(MultipleAgenciesError):
        export_agency_task(
            ExportTask(agency_id=AGENCY, run_id="r-1"), _settings(work_dir), work_dir
        )
    assert built == []


def test_the_task_runs_an_export_end_to_end(
    work_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = FakeDocumentSource()
    source.put(make_row("d1"), TEXT)
    patch_wiring(
        monkeypatch,
        source=source,
        sink=RecordingSink(),
        manifest_client=InMemoryStore(),
    )

    report = export_agency_task(
        ExportTask(agency_id=AGENCY, run_id="r-1", force=True, limit=5),
        _settings(work_dir),
        work_dir,
    )

    assert report.outcome == "success"
    assert report.new_count == 1
    assert report.forced is True
    assert report.limit == 5
    last = RunStateStore(work_dir).read()
    assert last is not None
    assert last.outcome == "success"


def test_a_failure_inside_the_export_is_recorded_once(
    work_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The coordinator reports its own failures; catch_error must not add a
    second record, or one failure would count as two toward the alert."""
    source = FakeDocumentSource()
    source.put(make_row("d1"), TEXT)
    source.fail_reads.add("d1")
    patch_wiring(
        monkeypatch,
        source=source,
        sink=RecordingSink(),
        manifest_client=InMemoryStore(),
    )

    report = export_agency_task(
        ExportTask(agency_id=AGENCY, run_id="r-1"), _settings(work_dir), work_dir
    )

    assert report.outcome == "failed"
    last = RunStateStore(work_dir).read()
    assert last is not None
    assert last.consecutive_failures == 1
    assert last.error_type == "StoreError"


# --- wiring -------------------------------------------------------------------


def test_wiring_builds_a_coordinator_from_settings(
    work_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from exporter.services.manifest_store import ManifestStore

    sink = RecordingSink(sink_id="object_store")
    monkeypatch.setattr(wiring, "build_sink", lambda _s, *, agency_id: sink)
    monkeypatch.setattr(
        wiring,
        "build_manifest_store",
        lambda _s: ManifestStore(InMemoryStore(), prefix="content"),
    )
    source = FakeDocumentSource()
    source.put(make_row("d1"), TEXT)

    coordinator = wiring.build_coordinator(
        _settings(work_dir),
        agency_id=AGENCY,
        work_dir=work_dir,
        run_state=None,
        source_factory=lambda _s: source,
    )

    assert coordinator.agency_id == AGENCY
    assert coordinator.export("r-1").outcome == "success"


# --- the multiprocessing target -----------------------------------------------


def _report(outcome: str) -> RunReport:
    return RunReport(
        run_id="r-1",
        agency_id=AGENCY,
        sink_id="object_store",
        outcome=outcome,
        started_at="2026-10-20T10:00:00Z",
        finished_at="2026-10-20T10:00:01Z",
    )


@pytest.mark.parametrize("outcome", ["success", "unchanged", "busy"])
def test_the_process_exits_cleanly_unless_the_run_failed(
    work_dir: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    monkeypatch.setattr(tasks, "export_agency_task", lambda *a, **k: _report(outcome))
    run_export_process(
        ExportTask(agency_id=AGENCY, run_id="r-1"), _settings(work_dir), work_dir
    )


def test_the_process_exits_1_on_a_failed_run(
    work_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tasks, "export_agency_task", lambda *a, **k: _report("failed"))
    with pytest.raises(SystemExit) as excinfo:
        run_export_process(
            ExportTask(agency_id=AGENCY, run_id="r-1"), _settings(work_dir), work_dir
        )
    assert excinfo.value.code == 1


def test_the_process_exits_1_without_a_traceback_on_a_raise(
    work_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> RunReport:
        raise ConfigurationError("not configured")

    monkeypatch.setattr(tasks, "export_agency_task", boom)
    with pytest.raises(SystemExit) as excinfo:
        run_export_process(
            ExportTask(agency_id=AGENCY, run_id="r-1"), _settings(work_dir), work_dir
        )
    assert excinfo.value.code == 1
    # `from None`: no chained exception for multiprocessing to print.
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True


# --- ExportTask ---------------------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"agency_id": "../x", "run_id": "r-1"},
        {"agency_id": AGENCY, "run_id": "a/b"},
        {"agency_id": AGENCY, "run_id": "r-1", "limit": 0},
    ],
)
def test_an_export_task_refuses_unsafe_input(fields: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        ExportTask(**fields)  # pyright: ignore[reportArgumentType]


def test_a_generated_run_id_is_a_valid_unique_segment() -> None:
    first, second = tasks.new_run_id(), tasks.new_run_id()
    assert first != second
    ExportTask(agency_id=AGENCY, run_id=first)
