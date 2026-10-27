"""D15 — the sink conformance suite: the ContentSink contract, asserted.

The same assertions run against every sink. This file is what makes "does the
llm-module implementation actually satisfy the contract?" a one-command
question: Stage L adds its stub as one more harness (L15) and inherits every
test below.

What is asserted, rather than read out of the code:

- an upsert reads back with the expected chunks, and a replay changes nothing;
- a metadata-only merge writes no chunk, when the capability is declared;
- a tail delete removes only the named ordinals and leaves every surviving
  chunk id unchanged;
- deleting something absent reports `missing`, never `failed`;
- F3 — no chunk is retrievable before its document's record, including when a
  write fails part-way;
- F4 — on delete, chunks go before the record, and a failed chunk delete keeps
  the record;
- a delete reported complete leaves nothing behind, stray objects included;
- an agency delete removes documents and nothing else (not the manifest);
- every declared capability works and every undeclared one raises
  NotSupported rather than silently no-op'ing.

HARNESSES. A harness gives the suite a sink plus a way to watch and break its
destination. Object-store sinks are observed through the store's call log
(InMemoryStore's own, or ObservedStore wrapped round a live S3Store), and the
keys in that log are parsed by THIS file's regex, independently of the sink's
own parser. The live variant needs CONFORMANCE_S3_* — see
tests/test_store_conformance.py.

The llm-module sink runs against the FastAPI stub (L15, L16), twice: under
AGREED_TERMS as they stand (no merge, no read-back, no agency delete, no
sweep) and under every term agreed, so each capability's declared-works and
undeclared-raises paths both run. It is observed through the fake behind the
stub, never through its own read-back. Tests that plant objects in a keyspace
skip it: it has none.
"""

import json
import re
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from exporter_fakes import (
    InMemoryStore,
    ObservedStore,
    RecordingSink,
    empty_prefix,
    live_s3_store_or_skip,
)
from llm_module_fake import TEST_COLLECTION, FakeLlmModule, session_for

from exporter.core.ids import make_chunk_id, manifest_key
from exporter.core.schemas import Chunk, DocumentRecord, TextSpan
from exporter.sinks.base import (
    ContentSink,
    DeleteOutcome,
    DocumentProbe,
    NotSupported,
    SinkCapabilities,
    UpsertStatus,
)
from exporter.sinks.llm_module_contract import AGREED_TERMS, ContractTerms
from exporter.sinks.llm_module_sink import LlmModuleSink
from exporter.sinks.object_store_sink import MAX_CONCURRENCY, ObjectStoreSink
from exporter.stores.base import StoreCapabilities

AGENCY = "agency-1"

# Recognises a document's record or chunk in any object-store key, whatever
# the prefix. Deliberately not the sink's parser: an independent reading.
_LAYOUT = re.compile(
    r".*/documents/(?P<document>[^/]+)/(?:metadata\.json|chunks/(?P<ordinal>[0-9]{5})\.json)"
)

# A write is ("put", document_id, ordinal | None); a delete the same with
# "delete". None is the document's record.
Event = tuple[str, str, int | None]


class Harness:
    """One sink, and the means to observe and break its destination.

    Observation never goes through the sink under test: a sink that cannot
    read back (the llm-module, until Open item 10) is still checked against
    what its destination actually holds.
    """

    sink: ContentSink

    def reset(self) -> None:
        raise NotImplementedError

    def events(self) -> list[Event]:
        raise NotImplementedError

    def probe(self, document_id: str) -> DocumentProbe | None:
        raise NotImplementedError

    def chunk_ids(self, document_id: str) -> dict[int, str]:
        raise NotImplementedError

    def fail_record_write(self, document_id: str) -> None:
        raise NotImplementedError

    def fail_chunk_write(self, document_id: str, ordinal: int) -> None:
        raise NotImplementedError

    def fail_chunk_delete(self, document_id: str, ordinal: int) -> None:
        raise NotImplementedError

    def heal(self) -> None:
        raise NotImplementedError

    def plant_stray(self, document_id: str, name: str) -> str:
        raise NotImplementedError

    def plant_manifest(self) -> str:
        raise NotImplementedError

    def holds(self, key: str) -> bool:
        raise NotImplementedError


@dataclass
class StoreHarness(Harness):
    """An object-store sink, observed through its store's call log."""

    sink: ContentSink
    store: InMemoryStore | ObservedStore
    prefix: str
    metadata_key: Callable[[str], str]
    chunk_key: Callable[[str, int], str]

    def probe(self, document_id: str) -> DocumentProbe | None:
        return self.sink.read_document(document_id)

    def holds(self, key: str) -> bool:
        return self.store.get_bytes(key) is not None

    def reset(self) -> None:
        self.store.calls.clear()

    def events(self) -> list[Event]:
        found: list[Event] = []
        for verb, target in self.store.calls:
            if verb not in ("put", "delete"):
                continue
            keys = target if isinstance(target, tuple) else (target,)
            for key in keys:
                match = _LAYOUT.fullmatch(str(key))
                if match is not None:
                    ordinal = match["ordinal"]
                    found.append(
                        (
                            verb,
                            match["document"],
                            None if ordinal is None else int(ordinal),
                        )
                    )
        return found

    def fail_record_write(self, document_id: str) -> None:
        self.store.fail_keys.add(self.metadata_key(document_id))

    def fail_chunk_write(self, document_id: str, ordinal: int) -> None:
        self.store.fail_keys.add(self.chunk_key(document_id, ordinal))

    def heal(self) -> None:
        self.store.fail_keys.clear()
        self.store.fail_delete_keys.clear()

    def fail_chunk_delete(self, document_id: str, ordinal: int) -> None:
        self.store.fail_delete_keys.add(self.chunk_key(document_id, ordinal))

    def chunk_ids(self, document_id: str) -> dict[int, str]:
        probe = self.sink.read_document(document_id)
        ordinals = probe.chunk_ordinals if probe and probe.chunk_ordinals else ()
        ids: dict[int, str] = {}
        for ordinal in ordinals:
            payload = self.store.get_bytes(self.chunk_key(document_id, ordinal))
            assert payload is not None
            ids[ordinal] = json.loads(payload)["chunk_id"]
        return ids

    def plant_stray(self, document_id: str, name: str) -> str:
        """Write an object under a document that matches no layout key — a
        leftover from an older layout, or something an operator put there."""
        key = self.metadata_key(document_id).rsplit("/", 1)[0] + "/" + name
        self.store.put_bytes(key, b"stray")
        return key

    def plant_manifest(self) -> str:
        """Write a manifest where an inherited manifest store would (D17)."""
        key = manifest_key(self.prefix, AGENCY)
        self.store.put_bytes(key, b"{}")
        return key


@dataclass
class LlmHarness(Harness):
    """The llm-module sink against the stub (L15, L16), observed through the
    fake behind it. Failures are item statuses the far side returns, so the
    sink's own 207 handling is what each scenario exercises."""

    sink: ContentSink
    fake: FakeLlmModule

    def reset(self) -> None:
        self.fake.events.clear()

    def events(self) -> list[Event]:
        return list(self.fake.events)

    def probe(self, document_id: str) -> DocumentProbe | None:
        held = self.fake.probe(document_id)
        if held is None:
            return None
        return DocumentProbe(
            document_id=document_id,
            record_present=held["record_present"],
            chunk_count=held["chunk_count"],
            chunk_ordinals=tuple(held["chunk_ordinals"]),
        )

    def chunk_ids(self, document_id: str) -> dict[int, str]:
        return {
            point["ordinal"]: chunk_id
            for chunk_id, point in self.fake.points_of(document_id).items()
        }

    def fail_record_write(self, document_id: str) -> None:
        self.fake.fail_always("record", 500)

    def fail_chunk_write(self, document_id: str, ordinal: int) -> None:
        self.fake.fail_always(make_chunk_id(AGENCY, document_id, ordinal), 500)

    def fail_chunk_delete(self, document_id: str, ordinal: int) -> None:
        # The llm-module deletes a document in one item: a chunk that will
        # not go fails the document's item, and its record stays (F4).
        self.fake.fail_always(document_id, 500)
        self.fake.fail_always(make_chunk_id(AGENCY, document_id, ordinal), 500)

    def heal(self) -> None:
        self.fake.heal()

    def plant_stray(self, document_id: str, name: str) -> str:
        pytest.skip("the llm-module has points, not a keyspace to plant in")

    def plant_manifest(self) -> str:
        pytest.skip("the llm-module never holds the manifest (D.2)")

    def holds(self, key: str) -> bool:
        pytest.skip("the llm-module has points, not keys")


# Every term the far side could agree, so each capability's promise runs.
_ALL_TERMS = ContractTerms(
    supports_metadata_only_merge=True,
    max_concurrency=2,
    status="agreed",
    agency_id_filterable=True,
    supports_agency_scoped_delete=True,
    supports_stale_sweep=True,
    supports_read_back=True,
    read_back_lists_ordinals=True,
)


def _llm_module_sink(fake: FakeLlmModule, terms: ContractTerms) -> LlmModuleSink:
    return LlmModuleSink(
        base_url="https://llm-module.invalid",
        collection=TEST_COLLECTION,
        agency_id=AGENCY,
        sink_id=f"llm_module:{TEST_COLLECTION}",
        run_id="conformance",
        token_provider=lambda: "conformance-token",
        ingest_schema_version=1,
        max_request_bytes=8_388_608,
        connect_timeout=5.0,
        read_timeout=120.0,
        terms=terms,
        session=session_for(fake, via_stub=True),
        sleep=lambda _seconds: None,
        rand=lambda: 0.5,
    )


def _llm_harness(terms: ContractTerms) -> LlmHarness:
    fake = FakeLlmModule()
    return LlmHarness(sink=_llm_module_sink(fake, terms), fake=fake)


def _object_store_harness(
    store: InMemoryStore | ObservedStore, prefix: str = "content"
) -> Harness:
    sink = ObjectStoreSink(
        store=store, prefix=prefix, agency_id=AGENCY, sink_id="object_store"
    )
    return StoreHarness(
        sink=sink,
        store=store,
        prefix=prefix,
        metadata_key=sink._metadata_key,
        chunk_key=sink._chunk_key,
    )


def _recording_harness() -> Harness:
    sink = RecordingSink(agency_id=AGENCY)
    return StoreHarness(
        sink=sink,
        store=sink.store,
        prefix=RecordingSink.PREFIX,
        metadata_key=sink._metadata_key,
        chunk_key=sink._chunk_key,
    )


@pytest.fixture(
    params=[
        "object_store[in-memory]",
        "recording-fake",
        "llm_module[stub, proposed terms]",
        "llm_module[stub, all terms]",
        "object_store[s3-live]",
    ]
)
def harness(request: pytest.FixtureRequest) -> Iterator[Harness]:
    if request.param == "object_store[in-memory]":
        yield _object_store_harness(InMemoryStore())
    elif request.param == "recording-fake":
        yield _recording_harness()
    elif request.param == "llm_module[stub, proposed terms]":
        yield _llm_harness(AGREED_TERMS)
    elif request.param == "llm_module[stub, all terms]":
        yield _llm_harness(_ALL_TERMS)
    else:
        live = live_s3_store_or_skip()
        prefix = f"conformance/{uuid.uuid4().hex}"
        yield _object_store_harness(ObservedStore(live), prefix)
        empty_prefix(live, f"{prefix}/")


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------


def _record(
    document_id: str = "d1", chunk_count: int = 3, *, title: str = "Õppetoetus"
) -> DocumentRecord:
    return DocumentRecord(
        document_id=document_id,
        source_base_id="s1",
        content_origin="cleaned",
        source_url="https://www.eesti.ee/teenus",
        source={"title": title},
        raw_sha256="r" * 64,
        content_sha256="c" * 64,
        metadata_sha256="m" * 64,
        chunk_count=chunk_count,
        synced_at="2026-10-01T00:00:00Z",
    )


def _chunks(document_id: str = "d1", count: int = 3) -> list[Chunk]:
    return [
        Chunk(
            chunk_id=make_chunk_id(AGENCY, document_id, ordinal),
            document_id=document_id,
            ordinal=ordinal,
            text=f"3. detsember, lõik {ordinal}",
            span=TextSpan(ordinal * 20, ordinal * 20 + 18),
        )
        for ordinal in range(count)
    ]


def _publish(harness: Harness, document_id: str = "d1", count: int = 3) -> None:
    outcome = harness.sink.upsert_document(
        _record(document_id, count), _chunks(document_id, count)
    )
    assert outcome.published


# --------------------------------------------------------------------------
# Publish
# --------------------------------------------------------------------------


def test_an_upserted_document_reads_back_with_its_chunks(harness: Harness) -> None:
    outcome = harness.sink.upsert_document(_record(chunk_count=3), _chunks(count=3))

    assert outcome.status is UpsertStatus.PUBLISHED
    assert outcome.chunks_written == 3
    probe = harness.probe("d1")
    assert probe is not None
    assert probe.record_present
    assert probe.chunk_count == 3
    assert probe.chunk_ordinals == (0, 1, 2)


def test_re_upserting_identical_input_changes_nothing(harness: Harness) -> None:
    _publish(harness)
    before = (harness.probe("d1"), harness.chunk_ids("d1"))

    _publish(harness)

    assert (harness.probe("d1"), harness.chunk_ids("d1")) == before
    assert harness.chunk_ids("d1") == {
        ordinal: make_chunk_id(AGENCY, "d1", ordinal) for ordinal in range(3)
    }


def test_a_metadata_only_merge_touches_no_chunk(harness: Harness) -> None:
    if not harness.sink.capabilities.supports_metadata_only_merge:
        pytest.skip("not declared; the NotSupported path is asserted below")
    _publish(harness)
    harness.reset()

    outcome = harness.sink.upsert_document(_record(title="Uus pealkiri"), None)

    assert outcome.published
    assert outcome.metadata_only
    assert [event for event in harness.events() if event[2] is not None] == []
    probe = harness.probe("d1")
    assert probe is not None
    assert probe.chunk_ordinals == (0, 1, 2)


# --------------------------------------------------------------------------
# F3 — no chunk before its record
# --------------------------------------------------------------------------


def test_every_document_is_written_record_first(harness: Harness) -> None:
    harness.reset()
    _publish(harness, "d1", 3)
    _publish(harness, "d2", 2)

    first_write: dict[str, int | None] = {}
    for verb, document_id, ordinal in harness.events():
        if verb == "put":
            first_write.setdefault(document_id, ordinal)
    assert first_write == {"d1": None, "d2": None}


def test_a_failed_record_write_leaves_no_chunk_retrievable(harness: Harness) -> None:
    harness.fail_record_write("d1")

    outcome = harness.sink.upsert_document(_record(), _chunks())

    assert not outcome.published
    assert outcome.chunks_written == 0
    harness.heal()
    assert harness.probe("d1") is None


def test_a_failed_chunk_write_leaves_every_written_chunk_beside_its_record(
    harness: Harness,
) -> None:
    harness.fail_chunk_write("d1", 1)

    outcome = harness.sink.upsert_document(_record(), _chunks())

    assert not outcome.published
    # Chunk 0 always lands. Chunk 2 lands too where one request carries the
    # document (the llm-module reads every item); a sink writing in order
    # stops at the failure.
    assert 1 <= outcome.chunks_written <= 2
    assert outcome.chunks_written == len(harness.chunk_ids("d1"))
    harness.heal()
    probe = harness.probe("d1")
    assert probe is not None
    assert probe.record_present


def test_a_failed_publish_is_completed_by_a_replay(harness: Harness) -> None:
    """The next run is the retry: same keys, same ids, no duplicates."""
    harness.fail_chunk_write("d1", 2)
    harness.sink.upsert_document(_record(), _chunks())
    harness.heal()

    _publish(harness)

    probe = harness.probe("d1")
    assert probe is not None
    assert probe.chunk_ordinals == (0, 1, 2)


# --------------------------------------------------------------------------
# Deletes and F4
# --------------------------------------------------------------------------


def test_a_tail_delete_keeps_every_surviving_chunk(harness: Harness) -> None:
    """Trigger (d): a document shrinks from 5 chunks to 3. The coordinator
    re-publishes the 3, then deletes ordinals 3 and 4 by coordinate (C16)."""
    _publish(harness, count=5)
    _publish(harness, count=3)

    outcome = harness.sink.delete_chunks("d1", [3, 4])

    assert sorted(outcome.deleted) == [3, 4]
    probe = harness.probe("d1")
    assert probe is not None
    assert probe.chunk_ordinals == (0, 1, 2)
    assert harness.chunk_ids("d1") == {
        ordinal: make_chunk_id(AGENCY, "d1", ordinal) for ordinal in range(3)
    }


def test_deleting_absent_chunks_reports_missing(harness: Harness) -> None:
    _publish(harness)
    assert harness.sink.delete_chunks("d1", [7]) == DeleteOutcome(missing=(7,))


def test_deleting_an_absent_document_reports_missing_not_failed(
    harness: Harness,
) -> None:
    assert harness.sink.delete_documents(["absent"]) == DeleteOutcome(
        missing=("absent",)
    )


def test_a_document_delete_removes_chunks_before_the_record(harness: Harness) -> None:
    _publish(harness)
    harness.reset()

    outcome = harness.sink.delete_documents(["d1"])

    assert outcome == DeleteOutcome(deleted=("d1",))
    deletes = [event for event in harness.events() if event[0] == "delete"]
    record_at = deletes.index(("delete", "d1", None))
    assert all(ordinal is not None for _, _, ordinal in deletes[:record_at])
    assert {ordinal for _, _, ordinal in deletes[:record_at]} == {0, 1, 2}
    assert harness.probe("d1") is None


def test_a_failed_chunk_delete_keeps_the_record(harness: Harness) -> None:
    _publish(harness)
    harness.fail_chunk_delete("d1", 1)

    outcome = harness.sink.delete_documents(["d1"])

    assert set(outcome.failed) == {"d1"}
    harness.heal()
    probe = harness.probe("d1")
    assert probe is not None
    assert probe.record_present


@pytest.mark.parametrize(
    "stray", ["notes.txt", "chunks/1.json", "chunks/00001.json.bak"]
)
def test_a_document_delete_leaves_nothing_behind(harness: Harness, stray: str) -> None:
    """A stray object under a document does not parse as a chunk, but a
    delete reported complete must not leave it orphaned beside no record."""
    _publish(harness)
    stray_key = harness.plant_stray("d1", stray)
    harness.reset()

    outcome = harness.sink.delete_documents(["d1"])

    assert outcome == DeleteOutcome(deleted=("d1",))
    assert not harness.holds(stray_key)
    deletes = [event for event in harness.events() if event[0] == "delete"]
    assert deletes[-1] == ("delete", "d1", None)


def test_an_agency_delete_leaves_no_stray_behind(harness: Harness) -> None:
    if not harness.sink.capabilities.supports_agency_scoped_delete:
        pytest.skip("not declared; the NotSupported path is asserted below")
    _publish(harness)
    stray_key = harness.plant_stray("d1", "notes.txt")

    outcome = harness.sink.delete_agency(AGENCY)

    assert outcome == DeleteOutcome(deleted=(AGENCY,))
    assert not harness.holds(stray_key)


def test_an_agency_delete_removes_documents_and_not_the_manifest(
    harness: Harness,
) -> None:
    """When the manifest store is inherited (D17) the manifest shares this
    prefix; it belongs to the manifest store and must survive a purge."""
    if not harness.sink.capabilities.supports_agency_scoped_delete:
        pytest.skip("not declared; the NotSupported path is asserted below")
    _publish(harness, "d1")
    _publish(harness, "d2")
    manifest = harness.plant_manifest()

    outcome = harness.sink.delete_agency(AGENCY)

    assert outcome == DeleteOutcome(deleted=(AGENCY,))
    assert harness.probe("d1") is None
    assert harness.probe("d2") is None
    assert harness.holds(manifest)


# --------------------------------------------------------------------------
# Capabilities: declared ones work, undeclared ones refuse
# --------------------------------------------------------------------------

# Capability -> the verb it gates, called against a published document "d1".
_GATED: dict[str, Callable[[ContentSink], object]] = {
    "can_list": lambda sink: sink.holds_agency_content(),
    "can_read_back": lambda sink: sink.read_document("d1"),
    "supports_metadata_only_merge": lambda sink: sink.upsert_document(_record(), None),
    "supports_agency_scoped_delete": lambda sink: sink.delete_agency(AGENCY),
}


def _knows_it_holds_content(sink: ContentSink) -> None:
    """F21's probe: true while a document is there, false once it is gone."""
    assert sink.holds_agency_content() is True
    assert sink.delete_documents(["d1"]).is_complete
    assert sink.holds_agency_content() is False


def _reads_back(sink: ContentSink) -> None:
    probe = sink.read_document("d1")
    assert probe is not None
    assert probe.record_present
    assert probe.chunk_ordinals == (0, 1, 2)


def _merges_metadata(sink: ContentSink) -> None:
    outcome = sink.upsert_document(_record(title="Uus pealkiri"), None)
    assert outcome.published
    assert outcome.metadata_only
    assert outcome.chunks_written == 0


def _deletes_the_agency(sink: ContentSink) -> None:
    assert sink.delete_agency(AGENCY) == DeleteOutcome(deleted=(AGENCY,))
    if sink.capabilities.can_read_back:
        assert sink.read_document("d1") is None


# Capability -> what declaring it promises, checked against a published "d1".
_PROMISES: dict[str, Callable[[ContentSink], None]] = {
    "can_list": _knows_it_holds_content,
    "can_read_back": _reads_back,
    "supports_metadata_only_merge": _merges_metadata,
    "supports_agency_scoped_delete": _deletes_the_agency,
}


def test_every_gated_capability_has_a_promise() -> None:
    assert sorted(_PROMISES) == sorted(_GATED)


def test_an_empty_destination_holds_no_content_whatever_sits_beside_it(
    harness: Harness,
) -> None:
    """F21: only a record or a chunk is content. A manifest an inherited
    manifest store keeps beside the documents (D17), or a stray object that
    parses as neither, must not make a fresh destination read as populated."""
    if not harness.sink.capabilities.can_list:
        pytest.skip("the probe needs a listing")
    assert harness.sink.holds_agency_content() is False
    harness.plant_manifest()
    harness.plant_stray("d1", "notes.txt")
    assert harness.sink.holds_agency_content() is False
    _publish(harness)
    assert harness.sink.holds_agency_content() is True


@pytest.mark.parametrize("capability", sorted(_PROMISES))
def test_a_declared_capability_works(harness: Harness, capability: str) -> None:
    if not getattr(harness.sink.capabilities, capability):
        pytest.skip(f"{capability} not declared by this sink")
    _publish(harness)
    _PROMISES[capability](harness.sink)


def _degraded_sinks() -> dict[str, ContentSink]:
    """Each sink kind with the capabilities it CAN lose turned off."""
    return {
        "object_store[no listing, no reads]": ObjectStoreSink(
            store=InMemoryStore(
                capabilities=StoreCapabilities(
                    can_delete=True,
                    can_list=False,
                    can_read=False,
                    supports_batch_delete=False,
                )
            ),
            prefix="content",
            agency_id=AGENCY,
            sink_id="object_store",
        ),
        "recording-fake[nothing declared]": RecordingSink(
            agency_id=AGENCY,
            capabilities=SinkCapabilities(
                can_list=False,
                can_read_back=False,
                supports_metadata_only_merge=False,
                supports_batch_delete=False,
                atomic_per_document=False,
                supports_agency_scoped_delete=False,
                max_concurrency=1,
            ),
        ),
        "llm_module[proposed terms]": _llm_module_sink(FakeLlmModule(), AGREED_TERMS),
    }


@pytest.mark.parametrize("variant", sorted(_degraded_sinks()))
@pytest.mark.parametrize("capability", sorted(_GATED))
def test_an_undeclared_capability_raises_not_supported(
    variant: str, capability: str
) -> None:
    sink = _degraded_sinks()[variant]
    if getattr(sink.capabilities, capability):
        pytest.skip(f"{variant} still declares {capability}")
    with pytest.raises(NotSupported):
        _GATED[capability](sink)


def test_an_object_store_sink_declares_only_what_its_store_can_do() -> None:
    """Honest declaration: no listing means no read-back and no agency
    delete, because both are built on a listing."""
    sink = _degraded_sinks()["object_store[no listing, no reads]"]
    assert sink.capabilities == SinkCapabilities(
        can_list=False,
        can_read_back=False,
        supports_metadata_only_merge=True,
        supports_batch_delete=False,
        atomic_per_document=False,
        supports_agency_scoped_delete=False,
        max_concurrency=MAX_CONCURRENCY,
    )


def test_an_object_store_sink_without_a_listing_refuses_whole_document_deletes() -> (
    None
):
    """No D0 field gates delete_documents, but finding a document's chunks
    needs a listing; `can_list=False` is the declaration, and the refusal
    comes before anything is deleted."""
    sink = _degraded_sinks()["object_store[no listing, no reads]"]
    assert isinstance(sink, ObjectStoreSink)
    store = sink.store
    assert isinstance(store, InMemoryStore)

    with pytest.raises(NotSupported, match="can_list"):
        sink.delete_documents(["d1"])
    assert store.calls == []


def test_sweep_stale_sweeps_or_refuses_and_never_silently_no_ops(
    harness: Harness,
) -> None:
    """No capability gates the sweep (the llm-module sweeps by synced_at
    without listing), so the contract is: do it, or say you cannot. A sweep
    that runs removes what is stale and absent from the keep list, and
    spares what the keep list names however old it is (L10)."""
    _publish(harness, "d1")
    _publish(harness, "d2")
    try:
        outcome = harness.sink.sweep_stale(AGENCY, datetime.now(UTC), ["d2"])
    except NotSupported:
        return
    assert outcome == DeleteOutcome(deleted=("d1",))
    assert harness.probe("d1") is None
    assert harness.probe("d2") is not None
