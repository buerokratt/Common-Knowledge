"""J1, J2 — the deferred-deletion drain, against the real coordinator and an
ObjectStoreSink over an in-memory store.

The setup every test starts from: an export deletes a document, the
destination refuses, and the run commits with the delete journalled as
unresolved. The drain is what retries it.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from exporter.services.drain_service import DeletionDrain
from exporter.services.locking import run_lock
from exporter.services.manifest_service import ManifestService
from exporter.services.manifest_store import ManifestStore
from exporter.services.operations import OperationStateStore
from exporter.stores.base import DeleteOutcome, StoreError
from exporter_ops_world import AGENCY, MANIFEST_PREFIX, OpsWorld, text


@pytest.fixture
def world(tmp_path: Path) -> OpsWorld:
    return OpsWorld(tmp_path=tmp_path)


def _failed_delete(world: OpsWorld) -> None:
    """d1 deleted in CKB; the destination refuses its delete in run-1."""
    world.publish(("d1", text(12)), ("d2", text(12)))
    world.move("d1", is_deleted=True)
    world.store.fail_delete_keys = set(world.keys_of("d1"))
    report = world.export("run-1")
    assert report.outcome == "success"
    assert report.deletions_unresolved == 1
    assert world.journal("run-1").is_closed()
    assert world.journal("run-1").pending_document_deletes() == {"d1": "is_deleted"}


def _delete_calls(world: OpsWorld) -> list[object]:
    return [keys for verb, keys in world.store.calls if verb == "delete"]


def test_a_dry_run_reports_the_pending_delete_and_touches_nothing(
    world: OpsWorld,
) -> None:
    _failed_delete(world)
    journal_before = world.journal("run-1").payload()
    world.store.calls.clear()
    mirrors_before = dict(world.manifest_client.objects)

    report = world.drain(dry_run=True)

    assert report.outcome == "dry_run"
    assert report.candidates == 1
    assert report.candidate_ids == ("d1",)
    assert _delete_calls(world) == []
    assert world.journal("run-1").payload() == journal_before
    assert world.manifest_client.objects.keys() == mirrors_before.keys()
    assert world.keys_of("d1")  # still there


def test_a_drain_deletes_what_the_export_could_not_and_records_it(
    world: OpsWorld,
) -> None:
    _failed_delete(world)
    world.store.fail_delete_keys.clear()

    report = world.drain("drain-1")

    assert report.outcome == "drained"
    assert (report.candidates, report.deleted, report.still_unresolved) == (1, 1, 0)
    assert world.keys_of("d1") == []
    assert world.keys_of("d2")  # untouched
    journal = world.journal("run-1")
    assert journal.pending_document_deletes() == {}
    drained = [e for e in journal.entries() if e.get("drained_by") == "drain-1"]
    assert [e["event"] for e in drained] == ["intent", "outcome"]


def test_a_drain_mirrors_the_journal_it_appended_to(world: OpsWorld) -> None:
    _failed_delete(world)
    world.store.fail_delete_keys.clear()

    world.drain()

    mirror = world.manifest_client.objects[
        "manifests/_control/deletions/pending-run-1.jsonl"
    ]
    assert mirror.payload == world.journal("run-1").payload()
    assert not world.journal("run-1").needs_mirror()


def test_after_a_drain_the_next_export_can_take_gate_1(world: OpsWorld) -> None:
    """A drained journal left needing a mirror would keep every export off
    Gate 1's shortcut; the drain mirrors it itself."""
    _failed_delete(world)
    world.store.fail_delete_keys.clear()
    world.drain()

    # run-1 withheld nothing it could vouch for, but its unresolved delete
    # kept the watermark back; one walking run settles that.
    world.export("run-2")
    assert world.export("run-3").outcome == "unchanged"


def test_a_delete_still_refused_stays_pending_for_the_next_drain(
    world: OpsWorld,
) -> None:
    _failed_delete(world)

    first = world.drain("drain-1")
    assert first.outcome == "drained"
    assert (first.deleted, first.still_unresolved) == (0, 1)
    assert world.journal("run-1").pending_document_deletes() == {"d1": "is_deleted"}

    world.store.fail_delete_keys.clear()
    second = world.drain("drain-2")
    assert (second.deleted, second.still_unresolved) == (1, 0)


def test_an_already_gone_document_counts_as_drained(world: OpsWorld) -> None:
    """ "Already gone" is success (D3): a delete that landed out of band, or a
    drain repeated after a crash, resolves the entry rather than failing."""
    _failed_delete(world)
    world.store.fail_delete_keys.clear()
    for key in world.keys_of("d1"):
        del world.store.objects[key]

    report = world.drain()

    assert (report.deleted, report.missing, report.still_unresolved) == (0, 1, 0)
    assert world.journal("run-1").pending_document_deletes() == {}


def test_a_document_republished_since_is_never_deleted(world: OpsWorld) -> None:
    """The re-check against the committed manifest: d1 was restored and
    re-published after its delete failed, so deleting it now would remove
    live content (flagged risk 8, in sequence)."""
    _failed_delete(world)
    world.store.fail_delete_keys.clear()
    world.move("d1", text(12), is_deleted=False, updated_at="2026-10-09T09:00:00Z")
    assert world.export("run-2").outcome == "success"
    manifest = world.manifest()
    assert manifest is not None and "d1" in manifest.documents
    world.store.calls.clear()

    report = world.drain()

    assert report.outcome == "nothing_pending"
    assert report.skipped_republished == 1
    assert _delete_calls(world) == []
    assert world.keys_of("d1")


def test_a_journal_no_run_has_closed_is_left_to_the_export(world: OpsWorld) -> None:
    world.publish(("d1", text(12)))
    dead = world.journal("dead-run")
    dead.intend_document_deletes({"d1": "is_deleted"})  # died here, uncommitted
    world.store.calls.clear()

    report = world.drain()

    assert report.outcome == "nothing_pending"
    assert report.awaiting_export == 1
    assert _delete_calls(world) == []
    assert world.keys_of("d1")


def test_tail_entries_are_counted_and_left_to_the_export(world: OpsWorld) -> None:
    """diff.record() keeps a document whose tail was not confirmed on its
    larger count, so the next export retries the tail itself."""
    world.publish(("d1", text(40)))
    world.move("d1", text(3))
    manifest = world.manifest()
    assert manifest is not None
    old_count = manifest.documents["d1"].chunk_count
    world.store.fail_delete_keys = {
        f"content/agencies/{AGENCY}/documents/d1/chunks/{n:05d}.json"
        for n in range(old_count)
    }
    assert world.export("run-1").outcome == "success"
    world.store.calls.clear()

    report = world.drain()

    assert report.tails_left_to_export == 1
    assert report.tails_left_to_reconcile == 0
    assert report.candidates == 0
    assert _delete_calls(world) == []


def test_a_drain_while_the_run_lock_is_held_is_busy_and_writes_nothing(
    world: OpsWorld,
) -> None:
    """Verification 13, drain variant."""
    _failed_delete(world)
    world.store.fail_delete_keys.clear()
    before = world.journal("run-1").payload()
    world.store.calls.clear()

    with run_lock(world.work_dir) as acquired:
        assert acquired
        report = world.drain()

    assert report.outcome == "busy"
    assert _delete_calls(world) == []
    assert world.journal("run-1").payload() == before


def test_no_manifest_fails_the_drain_rather_than_deleting_blind(
    world: OpsWorld,
) -> None:
    journal = world.journal("orphan")
    journal.intend_document_deletes({"d1": "is_deleted"})
    journal.close(closed_by="orphan")

    report = world.drain()

    assert report.outcome == "failed"
    assert report.error_type == "NoManifestToDrainError"
    assert _delete_calls(world) == []


def test_with_nothing_pending_the_drain_never_reads_the_manifest(
    world: OpsWorld,
) -> None:
    """Before the first export commits there is no manifest, and an hourly
    drain must not report `failed` for it. The manifest service here fails on
    any read (DIFF_ON_MISSING_MANIFEST=fail), so these outcomes also prove
    none happened. An export that died uncommitted is still counted."""
    world.journal("dead-run").intend_document_deletes({"d1": "is_deleted"})
    drain = DeletionDrain(
        sink=world.the_sink,
        manifests=ManifestService(
            ManifestStore(world.manifest_client, prefix=MANIFEST_PREFIX),
            sink_id=world.the_sink.sink_id,
            on_missing="fail",
        ),
        work_dir=world.work_dir,
        clock=world.clock,
    )

    report = drain.drain("drain-1", dry_run=False)
    dry = drain.drain("drain-2", dry_run=True)

    assert (report.outcome, report.awaiting_export) == ("nothing_pending", 1)
    assert (dry.outcome, dry.candidates) == ("dry_run", 0)
    assert _delete_calls(world) == []


def test_a_sink_failing_part_way_leaves_the_rest_pending_and_mirrored_later(
    world: OpsWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two journals to drain; the destination goes away on the second. The
    first journal's delete stands, the second's intent stays pending, and the
    next drain both finishes it and mirrors the journal the failed one
    appended to but never mirrored."""
    world.publish(("d1", text(12)), ("d2", text(12)), ("d3", text(3)))
    world.move("d1", is_deleted=True)
    world.store.fail_delete_keys = set(world.keys_of("d1"))
    assert world.export("run-1").deletions_unresolved == 1
    world.move("d2", is_deleted=True, updated_at="2026-10-09T09:00:00Z")
    world.store.fail_delete_keys = set(world.keys_of("d1")) | set(world.keys_of("d2"))
    assert world.export("run-2").deletions_unresolved == 1
    world.store.fail_delete_keys.clear()

    sink = world.the_sink
    real = sink.delete_documents
    calls: list[object] = []

    def flaky(document_ids: list[str]) -> DeleteOutcome[str]:
        calls.append(document_ids)
        if len(calls) == 2:
            raise StoreError("the destination went away", permanent=False)
        return real(document_ids)

    monkeypatch.setattr(sink, "delete_documents", flaky)
    failed = world.drain("drain-1")
    monkeypatch.undo()

    assert (failed.outcome, failed.error_type) == ("failed", "StoreError")
    assert world.keys_of("d1") == []
    assert world.journal("run-1").pending_document_deletes() == {}
    assert world.journal("run-1").needs_mirror()
    assert world.journal("run-2").pending_document_deletes() == {"d2": "is_deleted"}

    retried = world.drain("drain-2")

    assert (retried.outcome, retried.deleted) == ("drained", 1)
    assert world.keys_of("d2") == []
    assert world.keys_of("d3")
    assert not world.journal("run-1").needs_mirror()
    assert not world.journal("run-2").needs_mirror()


def test_a_lock_release_that_fails_returns_the_recorded_report(
    world: OpsWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The operation finished and was recorded; the report handed back is
    that recorded one, duration included, not the unstamped draft."""

    @contextmanager
    def lock_that_fails_on_release(work_dir: Path) -> Iterator[bool]:
        yield True
        raise OSError("release failed")

    monkeypatch.setattr(
        "exporter.services.operations.run_lock", lock_that_fails_on_release
    )

    report = world.drain("drain-1", dry_run=True)

    last = OperationStateStore(world.work_dir, "drain").read()
    assert last is not None
    assert report.outcome == last["outcome"] == "dry_run"
    assert report.duration_seconds == last["duration_seconds"]


def test_the_outcome_is_recorded_for_health(world: OpsWorld) -> None:
    _failed_delete(world)
    world.drain("drain-7", dry_run=True)

    last = OperationStateStore(world.work_dir, "drain").read()
    assert last is not None
    assert (last["outcome"], last["run_id"], last["candidates"]) == (
        "dry_run",
        "drain-7",
        1,
    )
    assert "candidate_ids" not in last
