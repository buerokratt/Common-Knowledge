"""Stage F, F1–F7 — the publish coordinator, end to end against fakes.

No network, no Docker, no database: a RecordingSink over an InMemoryStore for
the destination, a second InMemoryStore for the manifest store, and a
FakeDocumentSource for CKB. Ordering *within* a document (F3/F4) is the sink's
and is asserted by tests/test_sink_conformance.py; these tests assert what the
coordinator orders — which verb each bucket gets, what is journalled before
it, and that nothing commits unless the whole run got that far.
"""

import itertools
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

import pytest

from exporter.core.chunking import Chunker
from exporter.core.constants import CHUNK_PROFILES
from exporter.core.ids import chunker_fingerprint
from exporter.core.schemas import Chunk, DocumentRecord, Manifest, RunReport
from exporter.services.deletion_journal import JOURNAL_DIRNAME
from exporter.services.locking import run_lock
from exporter.services.manifest_service import ManifestService
from exporter.services.manifest_store import ManifestStore
from exporter.services import publish_service
from exporter.services.publish_service import (
    TOO_MANY_CHUNKS,
    UNCONFIRMED_FINGERPRINT,
    PublishCoordinator,
)
from exporter.services.run_state import RunStateStore
from exporter.sinks.base import ContentSink, UpsertOutcome
from exporter.sinks.object_store_sink import ObjectStoreSink
from exporter.stores.base import DeleteOutcome, StoreError
from exporter_fakes import (
    FULL_SINK_CAPABILITIES,
    FakeDocumentSource,
    InMemoryStore,
    RecordingSink,
    make_row,
)

AGENCY = "agency-1"
PROFILE = CHUNK_PROFILES["compact"]  # small chunks, so a few sentences make several
FP = chunker_fingerprint(PROFILE)
EARLY = "2026-10-01T09:00:00Z"
LATER = "2026-10-08T09:00:00Z"


def text(sentences: int, *, word: str = "taotlus") -> str:
    """Estonian-ish prose long enough to chunk: ~45 characters a sentence."""
    return " ".join(
        f"Lause {i} räägib sellest, kuidas {word} esitada." for i in range(sentences)
    )


class LoggingSink(RecordingSink):
    """A RecordingSink that also logs each D0 verb as the coordinator called
    it, so dispatch is asserted in sink vocabulary rather than in keys."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # pyright: ignore[reportArgumentType]
        self.verbs: list[tuple[str, str, object]] = []
        self.before_delete_documents: list[Callable[[], object]] = []
        self.before_delete_chunks: list[Callable[[], object]] = []

    def _upsert_document(
        self, record: DocumentRecord, chunks: tuple[Chunk, ...] | None
    ) -> UpsertOutcome:
        shape = None if chunks is None else len(chunks)
        self.verbs.append(("upsert", record.document_id, shape))
        return super()._upsert_document(record, chunks)

    def _delete_documents(self, document_ids: tuple[str, ...]) -> DeleteOutcome[str]:
        for hook in self.before_delete_documents:
            hook()
        self.verbs.extend(("delete_document", d, None) for d in document_ids)
        return super()._delete_documents(document_ids)

    def _delete_chunks(
        self, document_id: str, ordinals: tuple[int, ...]
    ) -> DeleteOutcome[int]:
        for hook in self.before_delete_chunks:
            hook()
        self.verbs.append(("delete_chunks", document_id, ordinals))
        return super()._delete_chunks(document_id, ordinals)


@dataclass
class World:
    tmp_path: Path
    sink: ContentSink
    source: FakeDocumentSource = field(default_factory=FakeDocumentSource)
    manifest_client: InMemoryStore = field(default_factory=InMemoryStore)
    on_missing: str = "first_run"
    max_chunks: int = 5000
    checkpoint_every: int = 250
    ticks: Iterator[int] = field(default_factory=itertools.count)

    @property
    def work_dir(self) -> Path:
        return self.tmp_path / "work"

    @property
    def manifest_store(self) -> ManifestStore:
        return ManifestStore(self.manifest_client, prefix="manifests")

    @property
    def run_state(self) -> RunStateStore:
        return RunStateStore(self.work_dir)

    def clock(self) -> str:
        n = next(self.ticks)
        return f"2026-10-19T10:{n // 60:02d}:{n % 60:02d}Z"

    def manifests(self) -> ManifestService:
        return ManifestService(
            self.manifest_store,
            sink_id=self.sink.sink_id,
            on_missing=self.on_missing,  # pyright: ignore[reportArgumentType]
        )

    def coordinator(self) -> PublishCoordinator:
        return PublishCoordinator(
            sink=self.sink,
            manifests=self.manifests(),
            source=self.source,
            chunker=Chunker.from_profile(PROFILE),
            chunker_fingerprint=FP,
            work_dir=self.work_dir,
            max_chunks_per_document=self.max_chunks,
            bootstrap_checkpoint_every=self.checkpoint_every,
            run_state=self.run_state,
            clock=self.clock,
        )

    def export(
        self, run_id: str = "run-1", *, force: bool = False, limit: int | None = None
    ) -> RunReport:
        return self.coordinator().export(run_id, force=force, limit=limit)

    def manifest(self) -> Manifest | None:
        return self.manifest_store.load(AGENCY)

    @property
    def verbs(self) -> list[tuple[str, str, object]]:
        assert isinstance(self.sink, LoggingSink)
        return self.sink.verbs


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path=tmp_path, sink=LoggingSink())


def _published(world: World, *documents: tuple[str, str]) -> Manifest:
    """Publish `documents` (id, text) in a first run and reset the verb log."""
    for document_id, body in documents:
        world.source.put(make_row(document_id, updated_at=EARLY), body)
    report = world.export("run-0")
    assert report.outcome == "success"
    world.verbs.clear()
    world.source.reads.clear()
    manifest = world.manifest()
    assert manifest is not None
    return manifest


def _moved(
    world: World,
    document_id: str,
    body: str | None = None,
    *,
    at: str = LATER,
    **row: object,
) -> None:
    """Move a row's updated_at (and optionally its text or row fields)."""
    current = world.source.documents[document_id]
    world.source.put(
        replace(current.row, updated_at=at, **row),  # type: ignore[arg-type]
        current.text if body is None else body,
        sidecar=dict(current.sidecar) if current.sidecar is not None else None,
    )


def _chunk_keys(world: World, document_id: str) -> list[str]:
    assert isinstance(world.sink, LoggingSink)
    prefix = f"content/agencies/{AGENCY}/documents/{document_id}/chunks/"
    return sorted(k for k in world.sink.store.objects if k.startswith(prefix))


class _CommitSwitch:
    """Patches ManifestService.commit to fail until `fail` is cleared. With
    `land`, the manifest is written before the failure is raised: the PUT
    landed but its response was lost."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, land: bool = False) -> None:
        self.fail = True
        real = ManifestService.commit

        def commit(service: ManifestService, manifest: Manifest) -> object:
            if not self.fail:
                return real(service, manifest)
            if land:
                real(service, manifest)
            raise StoreError("InjectedFailure on commit")

        monkeypatch.setattr(ManifestService, "commit", commit)


# --- first run and Gate 2 --------------------------------------------------


def test_a_first_run_publishes_every_ready_document_and_commits(world: World) -> None:
    world.source.put(make_row("d1"), text(30))
    world.source.put(make_row("d2"), text(3))

    report = world.export()

    assert report.outcome == "success"
    assert report.new_count == 2
    assert [verb for verb, _, _ in world.verbs] == ["upsert", "upsert"]
    assert all(shape is not None and shape > 0 for _, _, shape in world.verbs)
    manifest = world.manifest()
    assert manifest is not None
    assert manifest.sink_id == "recording"
    assert manifest.corpus_watermark == EARLY
    assert set(manifest.documents) == {"d1", "d2"}
    assert manifest.documents["d1"].chunk_count > 1
    assert manifest.documents["d1"].chunker_fingerprint == FP


def test_an_unchanged_second_run_reads_nothing_and_publishes_nothing(
    world: World,
) -> None:
    """Gate 2 is load-bearing: an unmoved row costs zero object reads."""
    first = _published(world, ("d1", text(30)), ("d2", text(3)))

    report = world.export("run-2")

    assert report.outcome == "success"
    assert report.unchanged_count == 2
    assert world.source.reads == []
    assert world.verbs == []
    second = world.manifest()
    assert second is not None
    assert second.documents == first.documents
    assert replace(second, committed_at="x") == replace(first, committed_at="x")


# --- per-bucket dispatch (F5) ----------------------------------------------


def test_content_changed_upserts_then_deletes_only_the_lost_tail(world: World) -> None:
    before = _published(world, ("d1", text(40)))
    old_count = before.documents["d1"].chunk_count
    _moved(world, "d1", text(8, word="toetus"))

    report = world.export("run-2")

    assert report.content_changed_count == 1
    (upsert, tail) = world.verbs
    assert upsert[0] == "upsert"
    new_count = upsert[2]
    assert isinstance(new_count, int) and new_count < old_count
    assert tail == ("delete_chunks", "d1", tuple(range(new_count, old_count)))
    manifest = world.manifest()
    assert manifest is not None
    assert manifest.documents["d1"].chunk_count == new_count
    assert manifest.documents["d1"].source_updated_at == LATER
    journal = (world.work_dir / JOURNAL_DIRNAME / "run-2.jsonl").read_text("utf-8")
    assert '"kind": "chunks"' in journal


def test_a_grown_document_deletes_no_tail(world: World) -> None:
    _published(world, ("d1", text(8)))
    _moved(world, "d1", text(40, word="toetus"))
    world.export("run-2")
    assert [verb for verb, _, _ in world.verbs] == ["upsert"]


def test_metadata_changed_is_a_record_only_upsert(world: World) -> None:
    before = _published(world, ("d1", text(30)))
    current = world.source.documents["d1"]
    world.source.put(
        replace(current.row, updated_at=LATER),
        current.text,
        sidecar={"source_url": current.row.url, "page_title": "Uus pealkiri"},
    )

    report = world.export("run-2")

    assert report.metadata_changed_count == 1
    assert world.verbs == [("upsert", "d1", None)]
    manifest = world.manifest()
    assert manifest is not None
    entry = manifest.documents["d1"]
    assert entry.chunk_count == before.documents["d1"].chunk_count
    assert entry.metadata_sha256 != before.documents["d1"].metadata_sha256


def test_metadata_changed_without_merge_is_a_counted_full_repush(
    tmp_path: Path,
) -> None:
    """L8: the cheap path became the expensive one, and the report says so."""
    sink = LoggingSink(
        capabilities=replace(FULL_SINK_CAPABILITIES, supports_metadata_only_merge=False)
    )
    world = World(tmp_path=tmp_path, sink=sink)
    before = _published(world, ("d1", text(30)))
    current = world.source.documents["d1"]
    world.source.put(
        replace(current.row, updated_at=LATER),
        current.text,
        sidecar={"source_url": current.row.url, "page_title": "Uus pealkiri"},
    )

    report = world.export("run-2")

    assert report.metadata_full_repush_count == 1
    assert world.verbs == [("upsert", "d1", before.documents["d1"].chunk_count)]


@pytest.mark.parametrize(
    "row_change",
    [{"is_deleted": True}, {"is_excluded": True}, {"status": "not_found"}],
    ids=["is_deleted", "is_excluded", "not_found"],
)
def test_each_deletion_signal_deletes_the_document(
    world: World, row_change: dict[str, object]
) -> None:
    _published(world, ("d1", text(10)), ("d2", text(10)))
    _moved(world, "d1", **row_change)

    report = world.export("run-2")

    assert report.deleted_count == 1
    assert world.verbs == [("delete_document", "d1", None)]
    manifest = world.manifest()
    assert manifest is not None
    assert set(manifest.documents) == {"d2"}


def test_absence_from_the_rows_deletes_the_document(world: World) -> None:
    _published(world, ("d1", text(10)), ("d2", text(10)))
    world.source.remove("d1")

    report = world.export("run-2")

    assert report.deleted_count == 1
    assert world.verbs == [("delete_document", "d1", None)]


def test_a_never_published_deleted_row_costs_nothing(world: World) -> None:
    world.source.put(make_row("d1", is_deleted=True), text(10))
    report = world.export()
    assert report.outcome == "success"
    assert report.deleted_count == 0
    assert world.verbs == []


def test_document_deletes_are_journalled_before_the_sink_is_asked(
    world: World,
) -> None:
    _published(world, ("d1", text(10)))
    _moved(world, "d1", is_deleted=True)
    journal_path = world.work_dir / JOURNAL_DIRNAME / "run-2.jsonl"
    seen: list[str] = []
    assert isinstance(world.sink, LoggingSink)
    world.sink.before_delete_documents.append(
        lambda: seen.append(journal_path.read_text("utf-8"))
    )

    world.export("run-2")

    assert len(seen) == 1
    assert '"event": "intent"' in seen[0]
    assert '"event": "outcome"' not in seen[0]


def test_a_document_not_ready_is_held_untouched(world: World) -> None:
    """C15/E12: mid-refresh, with a stale content URL still populated."""
    before = _published(world, ("d1", text(10)))
    _moved(world, "d1", status="scraping")

    report = world.export("run-2")

    assert report.skipped_count == 1
    assert world.verbs == []
    assert world.source.reads == []
    manifest = world.manifest()
    assert manifest is not None
    assert manifest.documents["d1"] == before.documents["d1"]


# --- failures that are outcomes, not exceptions ----------------------------


def test_a_failed_upsert_keeps_the_previous_entry_and_withholds_the_watermark(
    world: World,
) -> None:
    before = _published(world, ("d1", text(10)), ("d2", text(10)))
    _moved(world, "d1", text(12, word="toetus"))
    _moved(world, "d2", text(12, word="toetus"))
    assert isinstance(world.sink, LoggingSink)
    world.sink.store.fail_keys.add(
        "content/agencies/agency-1/documents/d1/metadata.json"
    )

    report = world.export("run-2")

    assert report.outcome == "success"
    assert report.publish_failed_count == 1
    manifest = world.manifest()
    assert manifest is not None
    assert manifest.documents["d1"] == before.documents["d1"]
    assert manifest.documents["d2"].source_updated_at == LATER
    assert manifest.corpus_watermark is None


def test_a_failed_new_document_stays_out_of_the_manifest(world: World) -> None:
    world.source.put(make_row("d1"), text(10))
    assert isinstance(world.sink, LoggingSink)
    world.sink.store.fail_keys.add(
        "content/agencies/agency-1/documents/d1/metadata.json"
    )

    report = world.export()

    assert report.publish_failed_count == 1
    manifest = world.manifest()
    assert manifest is not None
    assert "d1" not in manifest.documents


def test_a_failed_tail_delete_keeps_the_old_count_and_is_journalled(
    world: World,
) -> None:
    before = _published(world, ("d1", text(40)))
    old_count = before.documents["d1"].chunk_count
    _moved(world, "d1", text(8, word="toetus"))
    assert isinstance(world.sink, LoggingSink)
    world.sink.store.fail_delete_keys.add(
        f"content/agencies/agency-1/documents/d1/chunks/{old_count - 1:05d}.json"
    )

    report = world.export("run-2")

    assert report.deletions_unresolved == 1
    manifest = world.manifest()
    assert manifest is not None
    # Carried, so the next run recomputes the tail from what is live.
    assert manifest.documents["d1"] == before.documents["d1"]
    assert manifest.corpus_watermark is None
    journal = (world.work_dir / JOURNAL_DIRNAME / "run-2.jsonl").read_text("utf-8")
    assert '"unresolved": [{"item": ' in journal


def test_a_failed_document_delete_is_journalled_and_the_entry_leaves(
    world: World,
) -> None:
    _published(world, ("d1", text(10)))
    _moved(world, "d1", is_deleted=True)
    assert isinstance(world.sink, LoggingSink)
    world.sink.store.fail_delete_keys.add(
        "content/agencies/agency-1/documents/d1/chunks/00000.json"
    )

    report = world.export("run-2")

    assert report.outcome == "success"
    assert report.deletions_unresolved == 1
    manifest = world.manifest()
    assert manifest is not None
    assert "d1" not in manifest.documents  # the journal owns it now


def test_too_many_chunks_skips_and_holds(tmp_path: Path) -> None:
    world = World(tmp_path=tmp_path, sink=LoggingSink())
    before = _published(world, ("d1", text(3)))
    world.max_chunks = 2
    _moved(world, "d1", text(40, word="toetus"))

    report = world.export("run-2")

    assert report.skipped_count == 1
    assert report.skipped_reasons == {"d1": TOO_MANY_CHUNKS}
    assert world.verbs == []
    manifest = world.manifest()
    assert manifest is not None
    assert manifest.documents["d1"] == before.documents["d1"]
    # A raised limit must not meet a Gate 1 that skips the unmoved corpus.
    assert manifest.corpus_watermark is None


def test_a_read_time_skip_is_reported_with_its_reason(world: World) -> None:
    world.source.put(make_row("d1"), text(10), skip_reason="oversize")
    report = world.export()
    assert report.skipped_reasons == {"d1": "oversize"}
    assert world.verbs == []
    manifest = world.manifest()
    assert manifest is not None
    assert manifest.corpus_watermark is None


def test_a_rechunk_that_disagrees_with_the_published_count_holds_one_document(
    tmp_path: Path,
) -> None:
    """One document must not wedge the agency: it is held and marked, the rest
    publish, and the next run republishes it in full."""
    sink = LoggingSink(
        capabilities=replace(FULL_SINK_CAPABILITIES, supports_metadata_only_merge=False)
    )
    world = World(tmp_path=tmp_path, sink=sink)
    before = _published(world, ("d1", text(30)), ("d2", text(10)))
    real_count = before.documents["d1"].chunk_count
    skewed = replace(before.documents["d1"], chunk_count=real_count + 1)
    world.manifest_store.commit(
        replace(before, documents={**before.documents, "d1": skewed})
    )
    current = world.source.documents["d1"]
    world.source.put(
        replace(current.row, updated_at=LATER),
        current.text,
        sidecar={"source_url": current.row.url, "page_title": "Uus pealkiri"},
    )
    _moved(world, "d2", text(12, word="toetus"))

    report = world.export("run-2")

    assert report.outcome == "success"
    assert report.publish_failed_count == 1
    assert [d for v, d, _ in world.verbs if v == "upsert"] == ["d2"]
    manifest = world.manifest()
    assert manifest is not None
    assert manifest.documents["d1"].chunker_fingerprint == UNCONFIRMED_FINGERPRINT
    assert manifest.documents["d1"].chunk_count == real_count + 1
    assert manifest.documents["d2"].source_updated_at == LATER
    assert manifest.corpus_watermark is None

    world.verbs.clear()
    report = world.export("run-3")

    assert report.content_changed_count == 1
    assert world.verbs == [
        ("upsert", "d1", real_count),
        ("delete_chunks", "d1", (real_count,)),
    ]
    manifest = world.manifest()
    assert manifest is not None
    assert manifest.documents["d1"].chunker_fingerprint == FP
    assert manifest.documents["d1"].chunk_count == real_count
    assert manifest.corpus_watermark == LATER


# --- failures that abort: nothing commits (F6) -----------------------------


def test_a_mid_run_exception_commits_nothing(world: World) -> None:
    before = _published(world, ("d1", text(10)), ("d2", text(10)))
    _moved(world, "d1", text(12, word="toetus"))
    _moved(world, "d2", text(12, word="toetus"))
    world.source.fail_reads.add("d2")

    report = world.export("run-2")

    assert report.outcome == "failed"
    assert world.manifest() == before
    assert world.run_state.read() is not None
    assert world.run_state.read().outcome == "failed"  # type: ignore[union-attr]


def test_a_failed_commit_leaves_the_journal_on_disk(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _published(world, ("d1", text(10)))
    _moved(world, "d1", is_deleted=True)

    def refuse(self: ManifestService, manifest: Manifest) -> None:
        raise StoreError("InjectedFailure on commit")

    monkeypatch.setattr(ManifestService, "commit", refuse)

    report = world.export("run-2")

    assert report.outcome == "failed"
    assert world.manifest() == before
    journal = world.work_dir / JOURNAL_DIRNAME / "run-2.jsonl"
    assert journal.exists()
    # Not mirrored: the mirror follows a commit that never happened.
    assert not any("_control" in key for key in world.manifest_client.objects)


def test_a_sink_exception_mid_delete_commits_nothing_and_the_next_run_deletes(
    world: World,
) -> None:
    before = _published(world, ("d1", text(10)), ("d2", text(10)))
    _moved(world, "d1", is_deleted=True)
    assert isinstance(world.sink, LoggingSink)

    def explode() -> None:
        raise StoreError("InjectedFailure mid-delete")

    world.sink.before_delete_documents.append(explode)

    assert world.export("run-2").outcome == "failed"
    assert world.manifest() == before

    world.sink.before_delete_documents.clear()
    assert world.export("run-3").outcome == "success"
    assert world.verbs == [("delete_document", "d1", None)]
    manifest = world.manifest()
    assert manifest is not None
    assert set(manifest.documents) == {"d2"}


def test_the_commit_follows_every_destination_write(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _published(world, ("d1", text(40)), ("d2", text(10)))
    _moved(world, "d1", text(8, word="toetus"))
    _moved(world, "d2", is_deleted=True)
    verbs_at_commit: list[int] = []
    real = ManifestService.commit

    def commit(service: ManifestService, manifest: Manifest) -> object:
        verbs_at_commit.append(len(world.verbs))
        return real(service, manifest)

    monkeypatch.setattr(ManifestService, "commit", commit)

    assert world.export("run-2").outcome == "success"

    assert [verb for verb, _, _ in world.verbs] == [
        "upsert",
        "delete_chunks",
        "delete_document",
    ]
    assert verbs_at_commit == [3]


# --- replay after a run that died before its commit (F6, F7) ---------------

RESTORED = "2026-10-15T09:00:00Z"


def test_a_document_deleted_by_an_uncommitted_run_is_republished_when_restored(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The manifest still lists the document the destination lost; restored
    with the same content, it must not be called unchanged."""
    before = _published(world, ("d1", text(30)), ("d2", text(10)))
    count = before.documents["d1"].chunk_count
    _moved(world, "d1", is_deleted=True)
    switch = _CommitSwitch(monkeypatch)

    assert world.export("run-2").outcome == "failed"
    assert _chunk_keys(world, "d1") == []
    assert world.manifest() == before

    switch.fail = False
    _moved(world, "d1", at=RESTORED, is_deleted=False)
    world.verbs.clear()
    report = world.export("run-3")

    assert report.outcome == "success"
    assert report.content_changed_count == 1
    assert world.verbs == [("upsert", "d1", count)]
    assert len(_chunk_keys(world, "d1")) == count
    manifest = world.manifest()
    assert manifest is not None
    assert manifest.documents["d1"].chunker_fingerprint == FP
    assert manifest.documents["d2"] == before.documents["d2"]

    # Reconciled and closed: the next run trusts the manifest again.
    world.verbs.clear()
    assert world.export("run-4").unchanged_count == 2
    assert world.verbs == []


def test_a_document_still_deleted_after_an_uncommitted_run_is_deleted_again(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _published(world, ("d1", text(10)), ("d2", text(10)))
    _moved(world, "d1", is_deleted=True)
    switch = _CommitSwitch(monkeypatch)
    assert world.export("run-2").outcome == "failed"

    switch.fail = False
    world.verbs.clear()
    report = world.export("run-3")

    assert report.deleted_count == 1
    assert world.verbs == [("delete_document", "d1", None)]
    manifest = world.manifest()
    assert manifest is not None
    assert set(manifest.documents) == {"d2"}


def test_content_reverted_after_an_uncommitted_tail_delete_is_republished(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _published(world, ("d1", text(40)))
    old_count = before.documents["d1"].chunk_count
    _moved(world, "d1", text(8, word="toetus"))
    switch = _CommitSwitch(monkeypatch)
    assert world.export("run-2").outcome == "failed"
    assert len(_chunk_keys(world, "d1")) < old_count

    switch.fail = False
    _moved(world, "d1", text(40), at=RESTORED)
    world.export("run-3")

    assert len(_chunk_keys(world, "d1")) == old_count
    manifest = world.manifest()
    assert manifest is not None
    entry = manifest.documents["d1"]
    assert entry.content_sha256 == before.documents["d1"].content_sha256
    assert entry.chunker_fingerprint == FP


def test_a_commit_that_landed_but_reported_failure_costs_nothing_next_run(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The next run reads the manifest that landed, so nothing is distrusted,
    and it mirrors the journal the failed run never got to."""
    _published(world, ("d1", text(10)), ("d2", text(10)))
    _moved(world, "d1", is_deleted=True)
    switch = _CommitSwitch(monkeypatch, land=True)
    assert world.export("run-2").outcome == "failed"
    key = "manifests/_control/deletions/pending-run-2.jsonl"
    assert key not in world.manifest_client.objects

    switch.fail = False
    world.verbs.clear()
    assert world.export("run-3").outcome == "success"

    assert world.verbs == []
    local = (world.work_dir / JOURNAL_DIRNAME / "run-2.jsonl").read_bytes()
    assert b'"closed_by": "run-3"' in local
    assert world.manifest_client.objects[key].payload == local


def test_a_committed_run_id_is_not_reused(world: World) -> None:
    _published(world, ("d1", text(10)), ("d2", text(10)))
    _moved(world, "d1", is_deleted=True)
    assert world.export("run-2").outcome == "success"
    _moved(world, "d2", is_deleted=True)
    world.verbs.clear()

    assert world.export("run-2").outcome == "failed"
    assert world.verbs == []


def test_a_manifest_for_another_sink_fails_with_no_sink_call(tmp_path: Path) -> None:
    """F17: the new destination is not silently treated as published."""
    first = World(tmp_path=tmp_path, sink=LoggingSink(sink_id="llm_module"))
    _published(first, ("d1", text(10)))

    second = World(
        tmp_path=tmp_path,
        sink=LoggingSink(sink_id="recording"),
        source=first.source,
        manifest_client=first.manifest_client,
    )
    report = second.export("run-2")

    assert report.outcome == "failed"
    assert second.verbs == []
    assert first.source.reads == []


def test_a_missing_manifest_under_fail_publishes_nothing(tmp_path: Path) -> None:
    world = World(tmp_path=tmp_path, sink=LoggingSink(), on_missing="fail")
    world.source.put(make_row("d1"), text(10))

    report = world.export()

    assert report.outcome == "failed"
    assert world.verbs == []
    assert world.manifest() is None


def test_an_empty_enumeration_never_deletes_the_corpus(world: World) -> None:
    before = _published(world, ("d1", text(10)), ("d2", text(10)))
    world.source.remove("d1")
    world.source.remove("d2")

    report = world.export("run-2")

    assert report.outcome == "failed"
    assert world.verbs == []
    assert world.manifest() == before


# --- the lock (F2) ---------------------------------------------------------


def test_a_held_lock_reports_busy_and_writes_nothing(world: World) -> None:
    world.source.put(make_row("d1"), text(10))

    with run_lock(world.work_dir) as held:
        assert held is True
        report = world.export()

    assert report.outcome == "busy"
    assert world.verbs == []
    assert world.source.listed == 0
    assert world.manifest() is None
    last = world.run_state.read()
    assert last is not None and last.outcome == "busy"

    # Busy left nothing behind: the next run proceeds.
    assert world.export("run-2").outcome == "success"


def test_the_lock_is_released_after_a_failed_run(world: World) -> None:
    world.source.put(make_row("d1"), text(10))
    world.source.fail_reads.add("d1")
    assert world.export().outcome == "failed"

    with run_lock(world.work_dir) as held:
        assert held is True
    world.source.fail_reads.clear()
    assert world.export("run-2").outcome == "success"


def test_a_lock_that_cannot_be_opened_is_a_failed_run_not_a_raise(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    @contextmanager
    def unopenable(work_dir: Path) -> Iterator[bool]:
        raise PermissionError(f"cannot open the lock under {work_dir}")
        yield False  # pragma: no cover - makes this a generator

    monkeypatch.setattr(publish_service, "run_lock", unopenable)
    world.source.put(make_row("d1"), text(10))

    report = world.export()

    assert report.outcome == "failed"
    assert world.verbs == []
    last = world.run_state.read()
    assert last is not None and last.outcome == "failed"


@pytest.mark.parametrize("error", [RuntimeError, PermissionError])
def test_a_run_that_cannot_be_recorded_still_returns_its_report(
    world: World, monkeypatch: pytest.MonkeyPatch, error: type[Exception]
) -> None:
    """The run committed; losing its log line or last-run record must neither
    raise out of export() nor strip the duration from the report. OSError is
    parametrised because export() once returned the unstamped report on it."""

    def unrecordable(*_args: object, **_kwargs: object) -> None:
        raise error("the record could not be written")

    monkeypatch.setattr(RunStateStore, "record", unrecordable)
    world.source.put(make_row("d1"), text(10))

    report = world.export(force=True)

    assert report.outcome == "success"
    # Stamped by _finish, which the old OSError path skipped.
    assert report.forced is True
    assert report.duration_seconds > 0
    assert world.manifest() is not None


# --- the journal mirror (F7) -----------------------------------------------


def test_the_journal_is_mirrored_after_a_commit(world: World) -> None:
    _published(world, ("d1", text(10)))
    _moved(world, "d1", is_deleted=True)

    world.export("run-2")

    key = "manifests/_control/deletions/pending-run-2.jsonl"
    assert key in world.manifest_client.objects
    local = (world.work_dir / JOURNAL_DIRNAME / "run-2.jsonl").read_bytes()
    assert world.manifest_client.objects[key].payload == local


def test_a_failed_mirror_does_not_fail_a_committed_run(world: World) -> None:
    _published(world, ("d1", text(10)))
    _moved(world, "d1", is_deleted=True)
    world.manifest_client.fail_keys.add(
        "manifests/_control/deletions/pending-run-2.jsonl"
    )

    report = world.export("run-2")

    assert report.outcome == "success"
    manifest = world.manifest()
    assert manifest is not None
    assert "d1" not in manifest.documents

    # Retried by the next commit, not forgotten.
    world.manifest_client.fail_keys.clear()
    world.export("run-3")
    assert "manifests/_control/deletions/pending-run-2.jsonl" in (
        world.manifest_client.objects
    )


def test_tail_deletes_are_journalled_before_the_sink_is_asked(world: World) -> None:
    _published(world, ("d1", text(40)))
    _moved(world, "d1", text(8, word="toetus"))
    journal_path = world.work_dir / JOURNAL_DIRNAME / "run-2.jsonl"
    seen: list[str] = []
    assert isinstance(world.sink, LoggingSink)
    world.sink.before_delete_chunks.append(
        lambda: seen.append(journal_path.read_text("utf-8"))
    )

    world.export("run-2")

    assert len(seen) == 1
    assert '"kind": "chunks"' in seen[0]
    assert '"event": "outcome"' not in seen[0]


def test_a_run_with_no_deletions_mirrors_nothing(world: World) -> None:
    world.source.put(make_row("d1"), text(10))
    world.export()
    assert not any("_control" in key for key in world.manifest_client.objects)


# --- sink-blindness --------------------------------------------------------


def test_two_sinks_get_the_same_decisions_and_chunk_ids(tmp_path: Path) -> None:
    """Same rows, same manifest documents, whichever destination: the diff and
    the ids do not know which sink they feed."""
    store = InMemoryStore()
    sinks: list[ContentSink] = [
        LoggingSink(sink_id="one"),
        ObjectStoreSink(store=store, prefix="content", agency_id=AGENCY, sink_id="two"),
    ]
    manifests = []
    for index, sink in enumerate(sinks):
        world = World(tmp_path=tmp_path / str(index), sink=sink)
        world.source.put(make_row("d1"), text(30))
        world.source.put(make_row("d2", is_excluded=True), text(3))
        world.source.put(make_row("d3", status="in_review"), text(3))
        assert world.export().outcome == "success"
        manifests.append(world.manifest())
    assert manifests[0] is not None and manifests[1] is not None
    assert manifests[0].documents == manifests[1].documents
    recorded = sinks[0]
    assert isinstance(recorded, LoggingSink)
    ids_one = sorted(k for k in recorded.store.objects if "/chunks/" in k)
    ids_two = sorted(k for k in store.objects if "/chunks/" in k)
    assert ids_one == ids_two


def test_the_coordinator_refuses_a_manifest_service_for_another_sink(
    tmp_path: Path,
) -> None:
    world = World(tmp_path=tmp_path, sink=LoggingSink(sink_id="one"))
    manifests = ManifestService(
        world.manifest_store, sink_id="two", on_missing="first_run"
    )
    with pytest.raises(ValueError, match="different destinations"):
        PublishCoordinator(
            sink=world.sink,
            manifests=manifests,
            source=world.source,
            chunker=Chunker.from_profile(PROFILE),
            chunker_fingerprint=FP,
            work_dir=world.work_dir,
            max_chunks_per_document=10,
            bootstrap_checkpoint_every=250,
        )


class _CrashingSink(LoggingSink):
    """A LoggingSink whose upsert of a named document raises, as a dying
    process would: the run fails rather than recording an outcome."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.crash_on: set[str] = set()

    def _upsert_document(
        self, record: DocumentRecord, chunks: tuple[Chunk, ...] | None
    ) -> UpsertOutcome:
        if record.document_id in self.crash_on:
            raise StoreError("InjectedFailure on upsert")
        return super()._upsert_document(record, chunks)


def _commits(monkeypatch: pytest.MonkeyPatch) -> list[Manifest]:
    """Record every manifest committed, checkpoints included."""
    committed: list[Manifest] = []
    real = ManifestService.commit

    def commit(service: ManifestService, manifest: Manifest) -> object:
        committed.append(manifest)
        return real(service, manifest)

    monkeypatch.setattr(ManifestService, "commit", commit)
    return committed


# --- F15: first-run checkpoints ------------------------------------------------


def test_a_first_run_checkpoints_with_the_watermark_withheld(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(tmp_path=tmp_path, sink=LoggingSink(), checkpoint_every=2)
    for n in range(1, 6):
        world.source.put(make_row(f"d{n}"), text(3))
    committed = _commits(monkeypatch)

    report = world.export()

    assert report.outcome == "success"
    assert report.checkpoints_committed == 2
    checkpoints, final = committed[:-1], committed[-1]
    assert [len(m.documents) for m in checkpoints] == [2, 4]
    assert all(m.corpus_watermark is None for m in checkpoints)
    assert len(final.documents) == 5
    assert final.corpus_watermark == EARLY


def test_a_killed_bootstrap_resumes_from_its_last_checkpoint(tmp_path: Path) -> None:
    sink = _CrashingSink()
    world = World(tmp_path=tmp_path, sink=sink, checkpoint_every=2)
    for n in range(1, 6):
        world.source.put(make_row(f"d{n}"), text(3))
    sink.crash_on.add("d5")

    report = world.export("run-1")

    assert report.outcome == "failed"
    assert report.checkpoints_committed == 2
    manifest = world.manifest()
    assert manifest is not None
    assert set(manifest.documents) == {"d1", "d2", "d3", "d4"}
    assert manifest.corpus_watermark is None

    sink.crash_on.clear()
    world.source.reads.clear()
    report = world.export("run-2")

    assert report.outcome == "success"
    # Gate 2 spares everything the checkpoint recorded, at zero reads.
    assert world.source.reads == ["d5"]
    assert report.new_count == 1
    assert report.unchanged_count == 4
    assert report.checkpoints_committed == 0
    manifest = world.manifest()
    assert manifest is not None
    assert manifest.corpus_watermark == EARLY


def test_an_incremental_run_never_checkpoints(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _published(world, ("d1", text(3)))
    for n in range(2, 6):
        world.source.put(make_row(f"d{n}"), text(3))
    world.checkpoint_every = 1
    committed = _commits(monkeypatch)

    report = world.export("run-2")

    assert report.outcome == "success"
    assert report.new_count == 4
    assert report.checkpoints_committed == 0
    assert len(committed) == 1


# --- F10: --limit and --force -----------------------------------------------


def test_a_limited_first_run_publishes_the_slice_and_withholds_the_watermark(
    world: World,
) -> None:
    for n in range(1, 4):
        world.source.put(make_row(f"d{n}"), text(3))

    report = world.export(limit=1)

    assert report.outcome == "success"
    assert report.limit == 1
    assert report.new_count == 1
    assert dict(report.skipped_reasons) == {
        "d2": publish_service.LIMIT_REACHED,
        "d3": publish_service.LIMIT_REACHED,
    }
    assert world.source.reads == ["d1"]
    manifest = world.manifest()
    assert manifest is not None
    assert set(manifest.documents) == {"d1"}
    assert manifest.corpus_watermark is None


def test_a_limited_incremental_run_holds_the_rest_and_never_deletes_them(
    world: World,
) -> None:
    before = _published(world, ("d1", text(3)), ("d2", text(3)), ("d3", text(3)))
    _moved(world, "d1", text(5))
    _moved(world, "d2", text(5))
    _moved(world, "d3", is_deleted=True)

    report = world.export("run-2", limit=1)

    assert report.outcome == "success"
    assert report.content_changed_count == 1
    assert report.deleted_count == 1  # an explicit signal needs no read
    assert dict(report.skipped_reasons) == {"d2": publish_service.LIMIT_REACHED}
    assert world.source.reads == ["d1"]
    assert ("delete_document", "d2", None) not in world.verbs
    manifest = world.manifest()
    assert manifest is not None
    assert manifest.documents["d2"] == before.documents["d2"]
    assert "d3" not in manifest.documents
    # The other changed rows have not been read, so Gate 1 must not skip them.
    assert manifest.corpus_watermark is None

    report = world.export("run-3")

    assert report.content_changed_count == 1
    manifest = world.manifest()
    assert manifest is not None
    assert manifest.corpus_watermark == LATER


def test_a_limit_below_one_is_refused(world: World) -> None:
    with pytest.raises(ValueError, match="limit"):
        world.export(limit=0)


def test_force_is_echoed_in_the_report(world: World) -> None:
    world.source.put(make_row("d1"), text(3))
    assert world.export(force=True).forced is True


# --- F11: the report ----------------------------------------------------------


def test_the_report_carries_the_run_duration(world: World) -> None:
    world.source.put(make_row("d1"), text(3))
    report = world.export()
    assert report.duration_seconds > 0
    assert report.error_type is None


def test_a_failed_run_reports_and_records_its_error_type(world: World) -> None:
    world.source.put(make_row("d1"), text(3))
    world.source.fail_reads.add("d1")

    report = world.export()

    assert report.outcome == "failed"
    assert report.error_type == "StoreError"
    last = world.run_state.read()
    assert last is not None
    assert last.error_type == "StoreError"


def test_the_sync_timestamp_is_the_run_start(world: World) -> None:
    world.source.put(make_row("d1"), text(10))
    report = world.export()
    record = world.sink.store.get_bytes(  # type: ignore[attr-defined]
        "content/agencies/agency-1/documents/d1/metadata.json"
    )
    assert record is not None
    assert report.started_at.encode() in record
    datetime.fromisoformat(report.started_at.replace("Z", "+00:00"))
