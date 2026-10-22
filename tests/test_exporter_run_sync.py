"""F10 — `python -m exporter.run_sync`, the operations CLI.

Driven through main(argv) over the in-memory fakes: the report is JSON on
stdout (or a file), exit codes follow the run outcome, and a usage or
configuration error is exit 2 before anything runs.
"""

import json
from pathlib import Path

import pytest

from exporter.api.config import Settings
from exporter.run_sync import EXIT_FAILED, EXIT_OK, EXIT_USAGE, main
from exporter.services.run_state import RunStateStore
from exporter_fakes import (
    FakeDocumentSource,
    InMemoryStore,
    RecordingSink,
    make_row,
    patch_wiring,
)

AGENCY = "agency-1"
TEXT = "Lause räägib sellest, kuidas taotlus esitada. " * 5


@pytest.fixture
def work_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    path = tmp_path / "work"
    monkeypatch.setenv("CONTENT_WORK_DIR", path.as_posix())
    return path


@pytest.fixture
def source(monkeypatch: pytest.MonkeyPatch, work_dir: Path) -> FakeDocumentSource:
    source = FakeDocumentSource()
    for n in (1, 2, 3):
        source.put(make_row(f"d{n}"), TEXT)
    patch_wiring(
        monkeypatch,
        source=source,
        sink=RecordingSink(),
        manifest_client=InMemoryStore(),
    )
    return source


def test_a_run_writes_its_report_as_json_on_stdout(
    source: FakeDocumentSource, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--agency-id", AGENCY, "--run-id", "manual-1"])

    assert code == EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert report["outcome"] == "success"
    assert report["run_id"] == "manual-1"
    assert report["new_count"] == 3


def test_a_run_is_recorded_where_health_reads_it(
    source: FakeDocumentSource, work_dir: Path
) -> None:
    main(["--agency-id", AGENCY, "--run-id", "manual-1"])
    last = RunStateStore(work_dir.resolve()).read()
    assert last is not None
    assert last.run_id == "manual-1"


def test_limit_and_force_reach_the_run(
    source: FakeDocumentSource, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--agency-id", AGENCY, "--limit", "1", "--force"])

    assert code == EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert report["limit"] == 1
    assert report["forced"] is True
    assert report["new_count"] == 1
    assert report["skipped_count"] == 2
    assert report["run_id"]  # generated


def test_accept_existing_content_reaches_the_run(
    source: FakeDocumentSource,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """F21's override is the CLI's alone, and it arrives at the coordinator:
    a first run into a populated destination publishes instead of failing."""
    sink = RecordingSink()
    patch_wiring(monkeypatch, source=source, sink=sink, manifest_client=InMemoryStore())
    assert main(["--agency-id", AGENCY, "--run-id", "seed"]) == EXIT_OK
    capsys.readouterr()
    # A fresh manifest store: the destination is populated, the manifest gone.
    patch_wiring(monkeypatch, source=source, sink=sink, manifest_client=InMemoryStore())

    assert main(["--agency-id", AGENCY, "--run-id", "refused"]) == EXIT_FAILED
    assert json.loads(capsys.readouterr().out)["error_type"] == "NonEmptyDestination"

    code = main(
        ["--agency-id", AGENCY, "--run-id", "accepted", "--accept-existing-content"]
    )

    assert code == EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert report["accepted_existing_content"] is True
    assert report["first_run_probe"] == "overridden"


def test_the_report_can_go_to_a_file(
    source: FakeDocumentSource, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "report.json"
    code = main(["--agency-id", AGENCY, "--output-json", str(target)])

    assert code == EXIT_OK
    assert capsys.readouterr().out == ""
    assert json.loads(target.read_text(encoding="utf-8"))["outcome"] == "success"


def test_a_failed_run_exits_1_and_still_reports(
    source: FakeDocumentSource, capsys: pytest.CaptureFixture[str]
) -> None:
    source.fail_reads.add("d2")
    code = main(["--agency-id", AGENCY])

    assert code == EXIT_FAILED
    report = json.loads(capsys.readouterr().out)
    assert report["outcome"] == "failed"
    assert report["error_type"] == "StoreError"


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--agency-id", "a/b"],
        ["--agency-id", AGENCY, "--run-id", "../x"],
        ["--agency-id", AGENCY, "--limit", "0"],
        ["--agency-id", AGENCY, "--limit", "many"],
    ],
)
def test_a_usage_error_exits_2_and_runs_nothing(
    source: FakeDocumentSource,
    argv: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(argv) == EXIT_USAGE
    assert source.listed == 0
    assert capsys.readouterr().out == ""


def test_a_configuration_error_exits_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No CONTENT_WORK_DIR: refused before anything runs."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    assert main(["--agency-id", AGENCY]) == EXIT_USAGE


def test_an_unconfigured_destination_exits_2_naming_it(
    work_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The real wiring, unpatched apart from A17's network call: Stage E's
    source builds, and the destination's missing keys are the refusal."""
    monkeypatch.setattr("exporter.worker.tasks.require_single_agency", lambda _s: 1)

    assert main(["--agency-id", AGENCY]) == EXIT_USAGE
    assert "EXTERNAL_S3_ENDPOINT_URL" in caplog.text
    last = RunStateStore(work_dir.resolve()).read()
    assert last is not None
    assert last.error_type == "ConfigurationError"
