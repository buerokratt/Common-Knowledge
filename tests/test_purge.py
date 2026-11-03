"""J7, J8 — the purge, and `python -m exporter.run_ops`.

The purge is the diff with an empty `current`, dispatched through the sink
like any other deletion; these tests assert what it deletes, what it commits,
what it leaves to the drain, and that nothing reaches it without --confirm.
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from exporter.api.config import Settings
from exporter.cli import EXIT_FAILED, EXIT_OK, EXIT_USAGE
from exporter.run_ops import exit_code, main
from exporter.run_sync import main as run_sync
from exporter.services.locking import run_lock
from exporter.services.operations import OperationStateStore
from exporter.services.run_state import RunStateStore
from exporter.sinks.base import SinkCapabilities
from exporter_fakes import (
    FULL_SINK_CAPABILITIES,
    FakeDocumentSource,
    InMemoryStore,
    RecordingSink,
    make_row,
    patch_wiring,
)
from exporter_ops_world import AGENCY, OpsWorld, documents_prefix, text


@pytest.fixture
def world(tmp_path: Path) -> OpsWorld:
    return OpsWorld(tmp_path=tmp_path)


def _documents(world: OpsWorld) -> list[str]:
    return [key for key in world.store.objects if key.startswith(documents_prefix())]


def test_a_purge_deletes_every_recorded_document_and_commits_an_empty_manifest(
    world: OpsWorld,
) -> None:
    world.publish(("d1", text(12)), ("d2", text(3)))

    report = world.purge()

    assert report.outcome == "purged"
    assert (report.requested, report.deleted, report.unresolved) == (2, 2, 0)
    assert report.deletions_recorded == 2
    assert report.residue == "missing"  # nothing the manifest did not record
    assert report.residual == "verified_empty"
    assert _documents(world) == []
    manifest = world.manifest()
    assert manifest is not None
    assert manifest.documents == {}
    assert manifest.corpus_watermark is None


def test_a_purge_also_removes_what_the_manifest_never_recorded(
    world: OpsWorld,
) -> None:
    """The agency-scoped delete takes anything an uncommitted run left."""
    world.publish(("d1", text(3)))
    stray = documents_prefix("unrecorded") + "/metadata.json"
    world.store.objects[stray] = world.store.objects[
        documents_prefix("d1") + "/metadata.json"
    ]

    report = world.purge()

    assert report.outcome == "purged"
    assert report.residue == "deleted"
    assert _documents(world) == []


def test_the_manifest_and_its_journal_mirrors_survive_a_purge(world: OpsWorld) -> None:
    world.publish(("d1", text(3)))

    world.purge("purge-1")

    keys = set(world.manifest_client.objects)
    assert f"manifests/agencies/{AGENCY}/manifest.json" in keys
    assert "manifests/_control/deletions/pending-purge-1.jsonl" in keys


def test_a_delete_the_destination_refuses_is_left_to_the_drain(
    world: OpsWorld,
) -> None:
    world.publish(("d1", text(12)), ("d2", text(3)))
    world.store.fail_delete_keys = set(world.keys_of("d1"))

    report = world.purge()

    assert report.outcome == "incomplete"
    assert (report.deleted, report.unresolved) == (1, 1)
    assert report.unresolved_ids == ("d1",)
    assert world.journal("purge-1").pending_document_deletes() == {
        "d1": "absent_from_rows"
    }

    world.store.fail_delete_keys.clear()
    drained = world.drain()
    assert (drained.outcome, drained.deleted) == ("drained", 1)
    assert _documents(world) == []


def test_purging_an_empty_manifest_has_nothing_to_purge(world: OpsWorld) -> None:
    world.publish(("d1", text(3)))
    world.purge("purge-1")

    report = world.purge("purge-2")

    assert report.outcome == "nothing_to_purge"
    assert report.requested == 0


def test_the_next_export_republishes_what_ckb_still_holds(world: OpsWorld) -> None:
    """Why the runbook disables the export's schedule before a purge."""
    world.publish(("d1", text(3)), ("d2", text(3)))
    world.purge()

    report = world.export("run-after")

    assert report.outcome == "success"
    assert report.new_count == 2


def test_a_sink_that_cannot_list_reports_the_residual_as_unverifiable(
    world: OpsWorld,
) -> None:
    """J9: requested versus acknowledged is all there is to report, and the
    outcome does not claim more: `purged_unverified`, never `purged`."""
    world.publish(("d1", text(3)))
    capabilities: SinkCapabilities = replace(
        FULL_SINK_CAPABILITIES,
        can_list=False,
        can_read_back=False,
        supports_agency_scoped_delete=False,
    )
    blind = OpsWorld(
        tmp_path=world.tmp_path,
        store=world.store,
        manifest_client=world.manifest_client,
        sink=RecordingSink(
            agency_id=AGENCY,
            sink_id="object_store",
            store=world.store,
            capabilities=capabilities,
        ),
    )

    report = blind.purge()

    assert report.outcome == "purged_unverified"
    assert report.residue == "not_supported"
    assert report.residual == "unverifiable"
    assert report.deleted == 1


def test_a_purge_while_the_run_lock_is_held_is_busy(world: OpsWorld) -> None:
    world.publish(("d1", text(3)))
    world.store.calls.clear()

    with run_lock(world.work_dir) as acquired:
        assert acquired
        report = world.purge()

    assert report.outcome == "busy"
    assert world.store.calls == []


def test_no_manifest_fails_the_purge(world: OpsWorld) -> None:
    report = world.purge()

    assert report.outcome == "failed"
    assert report.error_type == "NoManifestToPurgeError"


def test_a_purge_is_recorded_as_its_own_outcome_not_the_exports(
    world: OpsWorld,
) -> None:
    world.publish(("d1", text(3)))
    world.purge("purge-9")

    last = OperationStateStore(world.work_dir, "purge").read()
    assert last is not None
    assert (last["outcome"], last["run_id"]) == ("purged", "purge-9")
    assert RunStateStore(world.work_dir).read() is None


# --- python -m exporter.run_ops ---------------------------------------------


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> RecordingSink:
    """Settings from a clean environment, the task wired to fakes, and one
    agency already exported, so every operation has a manifest to act on."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setenv("CONTENT_WORK_DIR", (tmp_path / "work").as_posix())
    source = FakeDocumentSource()
    source.put(make_row("d1"), text(12))
    sink = RecordingSink()
    patch_wiring(monkeypatch, source=source, sink=sink, manifest_client=InMemoryStore())
    assert run_sync(["--agency-id", AGENCY, "--run-id", "seed"]) == EXIT_OK
    sink.store.calls.clear()
    return sink


@pytest.mark.parametrize(
    "argv",
    [
        ["purge", "--agency-id", AGENCY],
        ["purge", "--agency-id", AGENCY, "--confirm", "agency-2"],
    ],
)
def test_a_purge_without_a_matching_confirm_starts_nothing(
    monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    """J8: refused before settings are read, a sink is built or a lock taken."""

    def must_not_load() -> None:
        raise AssertionError("settings were read for an unconfirmed purge")

    monkeypatch.setattr("exporter.run_ops.load_settings", must_not_load)

    assert main(argv) == EXIT_USAGE


def test_a_confirmed_purge_runs_and_writes_its_report(
    cli: RecordingSink, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    code = main(["purge", "--agency-id", AGENCY, "--confirm", AGENCY, "--run-id", "p"])

    assert code == EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert (report["outcome"], report["requested"]) == ("purged", 1)


def test_a_dry_drain_from_the_cli_deletes_nothing(
    cli: RecordingSink, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    code = main(["drain", "--agency-id", AGENCY, "--dry-run"])

    assert code == EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert (report["outcome"], report["dry_run"]) == ("dry_run", True)
    assert [verb for verb, _ in cli.store.calls if verb == "delete"] == []


def test_reconcile_from_the_cli_reports_clean(
    cli: RecordingSink, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    code = main(["reconcile", "--agency-id", AGENCY, "--repair"])

    assert code == EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert (report["outcome"], report["scope"], report["repair"]) == (
        "clean",
        "full",
        True,
    )


def test_a_failed_operation_exits_1(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setenv("CONTENT_WORK_DIR", (tmp_path / "work").as_posix())
    patch_wiring(
        monkeypatch,
        source=FakeDocumentSource(),
        sink=RecordingSink(),
        manifest_client=InMemoryStore(),
    )

    code = main(["reconcile", "--agency-id", AGENCY])

    assert code == EXIT_FAILED
    assert json.loads(capsys.readouterr().out)["outcome"] == "failed"


@pytest.mark.parametrize(
    ("operation", "outcome", "code"),
    [
        ("purge", "purged", EXIT_OK),
        ("purge", "purged_unverified", EXIT_OK),
        ("purge", "nothing_to_purge", EXIT_OK),
        # A purge that did not finish must stop `purge && next_step`.
        ("purge", "busy", EXIT_FAILED),
        ("purge", "incomplete", EXIT_FAILED),
        ("purge", "failed", EXIT_FAILED),
        # Drain and reconcile results are read from the report.
        ("drain", "busy", EXIT_OK),
        ("drain", "nothing_pending", EXIT_OK),
        ("reconcile", "drift", EXIT_OK),
        ("reconcile", "clean_partial", EXIT_OK),
        ("reconcile", "failed", EXIT_FAILED),
    ],
)
def test_exit_codes(operation: str, outcome: str, code: int) -> None:
    assert exit_code(operation, outcome) == code


def test_flags_belong_to_their_operation() -> None:
    assert main(["drain", "--agency-id", AGENCY, "--repair"]) == EXIT_USAGE
    assert main(["reconcile", "--agency-id", AGENCY, "--dry-run"]) == EXIT_USAGE
