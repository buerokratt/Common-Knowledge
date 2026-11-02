"""J4, J5, J9 — reconcile and the orphan tail sweep.

The destination is an ObjectStoreSink over an in-memory store, populated by
the real coordinator; drift is staged by editing the store underneath it. The
narrower scopes run the same reconciler against a RecordingSink over the same
store, with the listing (and then the read-back) declared away — the
capability, not the sink class, is what decides the scope.
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from exporter.services.locking import run_lock
from exporter.sinks.base import SinkCapabilities
from exporter_fakes import FULL_SINK_CAPABILITIES, RecordingSink, StoredObject
from exporter_ops_world import (
    AGENCY,
    OpsWorld,
    chunk_key,
    documents_prefix,
    metadata_key,
    text,
)


@pytest.fixture
def world(tmp_path: Path) -> OpsWorld:
    return OpsWorld(tmp_path=tmp_path)


def _count(world: OpsWorld, document_id: str) -> int:
    manifest = world.manifest()
    assert manifest is not None
    return manifest.documents[document_id].chunk_count


def _copy_chunk(world: OpsWorld, document_id: str, to_ordinal: int) -> str:
    """A chunk object at `to_ordinal`, as a crash part-way through an
    earlier, longer publish would have left it."""
    key = chunk_key(document_id, to_ordinal)
    world.store.objects[key] = world.store.objects[chunk_key(document_id, 0)]
    return key


def _declare(world: OpsWorld, document_id: str, chunk_count: int) -> None:
    """Rewrite the destination record's own chunk_count."""
    key = metadata_key(document_id)
    stored = world.store.objects[key]
    record = json.loads(stored.payload)
    record["chunk_count"] = chunk_count
    world.store.objects[key] = StoredObject(
        json.dumps(record).encode("utf-8"), stored.content_type, stored.metadata
    )


def _deletes(world: OpsWorld) -> list[object]:
    return [keys for verb, keys in world.store.calls if verb == "delete"]


# --- full scope: a sink that can list ---------------------------------------


def test_a_destination_that_matches_the_manifest_is_clean(world: OpsWorld) -> None:
    world.publish(("d1", text(12)), ("d2", text(3)))

    report = world.reconcile()

    assert report.outcome == "clean"
    assert report.scope == "full"
    assert report.manifest_documents == report.destination_documents == 2
    assert report.manifest_chunks == _count(world, "d1") + _count(world, "d2")


def test_each_category_of_drift_is_counted_and_named(world: OpsWorld) -> None:
    world.publish(("gone", text(3)), ("no-record", text(3)), ("gap", text(12)))
    for key in world.keys_of("gone"):
        del world.store.objects[key]
    del world.store.objects[metadata_key("no-record")]
    del world.store.objects[chunk_key("gap", 0)]
    world.store.objects[metadata_key("ghost")] = world.store.objects[
        metadata_key("gap")
    ]

    report = world.reconcile()

    assert report.outcome == "drift"
    assert (
        report.missing_documents,
        report.record_missing,
        report.chunk_gaps,
        report.unrecorded_documents,
        report.orphan_tail_documents,
    ) == (1, 1, 1, 1, 0)
    assert report.drift_ids == {
        "missing_documents": ("gone",),
        "record_missing": ("no-record",),
        "chunk_gaps": ("gap",),
        "unrecorded_documents": ("ghost",),
    }


def test_an_orphan_tail_is_reported_and_left_without_repair(
    world: OpsWorld,
) -> None:
    world.publish(("d1", text(12)))
    orphan = _copy_chunk(world, "d1", _count(world, "d1"))

    report = world.reconcile()

    assert report.outcome == "drift"
    assert (report.orphan_tail_documents, report.orphan_tail_chunks) == (1, 1)
    assert orphan in world.store.objects
    assert _deletes(world) == []


def test_repair_removes_only_the_orphaned_tail(world: OpsWorld) -> None:
    """J5: ordinals at or past the count go; every live chunk stays."""
    world.publish(("d1", text(12)), ("d2", text(12)))
    count = _count(world, "d1")
    orphans = [_copy_chunk(world, "d1", count), _copy_chunk(world, "d1", count + 3)]
    live = set(world.store.objects) - set(orphans)

    report = world.reconcile(repair=True)

    assert report.outcome == "repaired"
    assert (report.tails_repaired, report.tails_unresolved) == (1, 0)
    assert set(world.store.objects) == live
    assert world.reconcile("reconcile-2").outcome == "clean"


def test_repair_never_deletes_a_chunk_the_destination_record_still_counts(
    world: OpsWorld,
) -> None:
    """The manifest can be behind the destination: an upsert that landed but
    was never confirmed leaves the old, smaller count in the manifest while
    the record — written first (F3) — states the new one. The tail is
    measured from the larger of the two."""
    world.publish(("d1", text(12)))
    count = _count(world, "d1")
    live_past_manifest = _copy_chunk(world, "d1", count)
    _declare(world, "d1", count + 1)
    orphan = _copy_chunk(world, "d1", count + 1)

    report = world.reconcile(repair=True)

    assert report.orphan_tail_chunks == 1
    assert live_past_manifest in world.store.objects
    assert orphan not in world.store.objects


def test_a_record_whose_count_cannot_be_read_is_never_repaired(
    world: OpsWorld,
) -> None:
    world.publish(("d1", text(12)))
    orphan = _copy_chunk(world, "d1", _count(world, "d1"))
    stored = world.store.objects[metadata_key("d1")]
    world.store.objects[metadata_key("d1")] = replace(stored, payload=b"not json")

    report = world.reconcile(repair=True)

    assert report.outcome == "drift"
    assert report.orphan_tail_documents == 1
    assert orphan in world.store.objects


def test_listing_entries_that_are_not_records_or_chunks_are_ignored(
    world: OpsWorld,
) -> None:
    """D12: parse, never count. A gateway's pseudo-directory entry and a
    stray object are not chunks, and a sweep that counted them would delete
    live content."""
    world.publish(("d1", text(12)))
    stray = world.store.objects[metadata_key("d1")]
    world.store.objects[documents_prefix("d1") + "/chunks/"] = stray
    world.store.objects[documents_prefix("d1") + "/chunks/7.json"] = stray
    world.store.objects[documents_prefix("d1") + "/notes.txt"] = stray

    assert world.reconcile(repair=True).outcome == "clean"
    assert _deletes(world) == []


def test_after_a_repair_the_next_export_does_not_republish_the_document(
    world: OpsWorld,
) -> None:
    """The repair's journal is closed and mirrored, so the export neither
    distrusts the repaired document nor is kept off Gate 1."""
    world.publish(("d1", text(12)))
    world.export("run-1")  # settles the first run's watermark
    _copy_chunk(world, "d1", _count(world, "d1"))
    world.reconcile(repair=True)
    assert world.journal("reconcile-1").is_closed()
    assert not world.journal("reconcile-1").needs_mirror()

    assert world.export("run-2").outcome == "unchanged"


def test_a_refused_orphan_tail_is_left_to_the_next_repair_not_the_export(
    world: OpsWorld,
) -> None:
    """No manifest counted these ordinals, so no export will ever retry them.
    The drain says so (`tails_left_to_reconcile`, not `tails_left_to_export`),
    and the next repair, working from its own listing, removes them."""
    world.publish(("d1", text(12)))
    orphan = _copy_chunk(world, "d1", _count(world, "d1"))
    world.store.fail_delete_keys = {orphan}

    report = world.reconcile("reconcile-1", repair=True)

    assert report.outcome == "drift"
    assert (report.tails_repaired, report.tails_unresolved) == (0, 1)
    assert orphan in world.store.objects
    assert world.journal("reconcile-1").is_closed()
    drained = world.drain("drain-1")
    assert (drained.tails_left_to_reconcile, drained.tails_left_to_export) == (1, 0)

    world.store.fail_delete_keys.clear()
    retried = world.reconcile("reconcile-2", repair=True)

    assert retried.outcome == "repaired"
    assert orphan not in world.store.objects
    assert world.reconcile("reconcile-3").outcome == "clean"


def test_an_unrecorded_document_is_reported_never_repaired(world: OpsWorld) -> None:
    world.publish(("d1", text(3)))
    ghost = metadata_key("ghost")
    world.store.objects[ghost] = world.store.objects[metadata_key("d1")]

    report = world.reconcile(repair=True)

    assert report.outcome == "drift"
    assert report.unrecorded_documents == 1
    assert ghost in world.store.objects


# --- narrower scopes --------------------------------------------------------


def _narrow(world: OpsWorld, **declared: bool) -> OpsWorld:
    """The same store and manifest, reached through a sink that declares
    less. RecordingSink shares the object-store key layout."""
    capabilities: SinkCapabilities = replace(FULL_SINK_CAPABILITIES, **declared)
    narrowed = OpsWorld(
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
    return narrowed


def test_a_sink_that_reads_back_but_cannot_list_checks_the_manifest_only(
    world: OpsWorld,
) -> None:
    """No drift among the manifest's documents is `clean_partial`, never
    `clean`: the ghost is at the destination, and nothing here looked."""
    world.publish(("d1", text(12)), ("d2", text(3)))
    world.store.objects[metadata_key("ghost")] = world.store.objects[metadata_key("d1")]
    reader = _narrow(world, can_list=False)

    report = reader.reconcile()

    assert report.scope == "manifest_documents"
    assert report.outcome == "clean_partial"
    assert report.unrecorded_documents == 0


def test_clean_is_only_ever_a_full_scope_outcome(world: OpsWorld) -> None:
    """The same untouched destination, reached two ways: listed it is
    `clean`; read back it is `clean_partial`."""
    world.publish(("d1", text(12)))

    assert world.reconcile("reconcile-1").outcome == "clean"
    narrowed = _narrow(world, can_list=False).reconcile("reconcile-2")
    assert (narrowed.outcome, narrowed.scope) == ("clean_partial", "manifest_documents")


def test_read_back_still_finds_drift_in_what_the_manifest_lists(
    world: OpsWorld,
) -> None:
    world.publish(("d1", text(12)))
    orphan = _copy_chunk(world, "d1", _count(world, "d1"))
    reader = _narrow(world, can_list=False)

    report = reader.reconcile(repair=True)

    assert report.outcome == "drift"
    assert report.orphan_tail_documents == 1
    # Repair needs a listing: nothing is deleted at this scope.
    assert orphan in world.store.objects
    assert report.tails_repaired == 0


def test_a_sink_that_can_neither_list_nor_read_back_is_unverifiable_never_clean(
    world: OpsWorld,
) -> None:
    """J9, Verification 26."""
    world.publish(("d1", text(12)))
    blind = _narrow(world, can_list=False, can_read_back=False)

    report = blind.reconcile(repair=True)

    assert report.outcome == "unverifiable"
    assert report.scope == "none"
    assert report.manifest_documents == 1
    assert report.manifest_chunks == _count(world, "d1")
    assert _deletes(world) == []


# --- the lock and the manifest ----------------------------------------------


def test_a_reconcile_while_the_run_lock_is_held_is_busy(world: OpsWorld) -> None:
    world.publish(("d1", text(3)))
    world.store.calls.clear()

    with run_lock(world.work_dir) as acquired:
        assert acquired
        report = world.reconcile()

    assert report.outcome == "busy"
    assert world.store.calls == []


def test_no_manifest_fails_rather_than_reporting_an_empty_destination(
    world: OpsWorld,
) -> None:
    report = world.reconcile()

    assert report.outcome == "failed"
    assert report.error_type == "NoManifestToReconcileError"
