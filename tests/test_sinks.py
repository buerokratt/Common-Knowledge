"""D0 — the ContentSink contract, its outcome types and the sink factory.

Pure: a recording fake sink over an in-memory store, no Docker, no network.
These pin the rules the ContentSink base class enforces for every sink. The
sink conformance suite (D15) builds on them and runs the same behavioural
assertions against each real sink as it lands.
"""

import ast
import dataclasses
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path

import pytest
from exporter_fakes import (
    FULL_SINK_CAPABILITIES,
    FULL_STORE_CAPABILITIES,
    InMemoryStore,
    RecordingSink,
)
from pydantic import SecretStr

from exporter.api.config import ConfigurationError, Settings
from exporter.core.ids import make_chunk_id
from exporter.core.schemas import Chunk, DocumentRecord, TextSpan
from exporter.sinks.base import (
    ContentSink,
    DeleteOutcome,
    DocumentProbe,
    NotSupported,
    SinkCapabilities,
    StoreError,
    UpsertOutcome,
    UpsertStatus,
)
from exporter.sinks.factory import build_sink
from exporter.sinks.object_store_sink import ObjectStoreSink
from exporter.stores.base import UNREPORTED_DELETE_REASON, StoreCapabilities
from exporter.stores.s3_store import S3Store

EXPORTER_ROOT = Path(__file__).resolve().parents[1] / "content-external" / "exporter"
AGENCY = "agency-1"


def _record(document_id: str = "d1", chunk_count: int = 2) -> DocumentRecord:
    return DocumentRecord(
        document_id=document_id,
        source_base_id="s1",
        content_origin="cleaned",
        source_url="https://www.eesti.ee/teenus",
        source={"title": "Õppetoetus"},
        raw_sha256="r",
        content_sha256="c",
        metadata_sha256="m",
        chunk_count=chunk_count,
        synced_at="2026-10-12T00:00:00Z",
    )


def _chunks(document_id: str = "d1", count: int = 2) -> list[Chunk]:
    return [
        Chunk(
            chunk_id=make_chunk_id(AGENCY, document_id, ordinal),
            document_id=document_id,
            ordinal=ordinal,
            text=f"lõik {ordinal}",
            span=TextSpan(ordinal * 10, ordinal * 10 + 6),
        )
        for ordinal in range(count)
    ]


def _sink(**capabilities: bool) -> RecordingSink:
    values = dataclasses.asdict(FULL_SINK_CAPABILITIES) | capabilities
    return RecordingSink(agency_id=AGENCY, capabilities=SinkCapabilities(**values))


def _store_capabilities(**capabilities: bool) -> StoreCapabilities:
    return StoreCapabilities(
        **(dataclasses.asdict(FULL_STORE_CAPABILITIES) | capabilities)
    )


def _published(sink: RecordingSink, document_id: str = "d1", count: int = 2) -> None:
    sink.upsert_document(_record(document_id, count), _chunks(document_id, count))


def _put_keys(store: InMemoryStore) -> list[str]:
    return [str(key) for verb, key in store.calls if verb == "put"]


def _deleted_keys(store: InMemoryStore) -> list[str]:
    return [
        key
        for verb, keys in store.calls
        if verb == "delete" and isinstance(keys, tuple)
        for key in keys
    ]


# --------------------------------------------------------------------------
# The shapes
# --------------------------------------------------------------------------


def test_sink_capabilities_declares_exactly_the_six_d0_fields() -> None:
    """Widening the capability set is a parity-matrix change and must be a
    visible act, not a quiet field addition."""
    assert [f.name for f in dataclasses.fields(SinkCapabilities)] == [
        "can_list",
        "can_read_back",
        "supports_metadata_only_merge",
        "supports_batch_delete",
        "atomic_per_document",
        "supports_agency_scoped_delete",
    ]


def test_upsert_status_is_the_f18_vocabulary() -> None:
    assert {status.value for status in UpsertStatus} == {
        "published",
        "failed_permanent",
        "failed_retryable",
        "deferred",
    }


def test_a_metadata_only_upsert_writes_no_chunks() -> None:
    with pytest.raises(ValueError, match="metadata-only"):
        UpsertOutcome(
            "d1", UpsertStatus.PUBLISHED, chunks_written=2, metadata_only=True
        )


def test_upsert_detail_is_sanitised() -> None:
    outcome = UpsertOutcome(
        "d1",
        UpsertStatus.FAILED_PERMANENT,
        chunks_written=0,
        metadata_only=False,
        detail="POST https://svc:hunter2@llm.example/ingest -> 401",
    )
    assert outcome.detail is not None
    assert "hunter2" not in outcome.detail
    assert not outcome.published


def test_a_probe_whose_ordinals_disagree_with_its_count_is_refused() -> None:
    with pytest.raises(ValueError, match="disagrees"):
        DocumentProbe("d1", record_present=True, chunk_count=3, chunk_ordinals=(0, 1))


def test_the_sink_layer_re_exports_the_store_vocabulary() -> None:
    """The coordinator imports destination vocabulary from one place."""
    from exporter.stores import base as store_base

    assert DeleteOutcome is store_base.DeleteOutcome
    assert NotSupported is store_base.NotSupported


# --------------------------------------------------------------------------
# Construction
# --------------------------------------------------------------------------


def test_a_sink_missing_a_hook_cannot_be_instantiated() -> None:
    class Incomplete(ContentSink):
        @property
        def capabilities(self) -> SinkCapabilities:
            return FULL_SINK_CAPABILITIES

    with pytest.raises(TypeError, match="abstract"):
        Incomplete(agency_id=AGENCY, sink_id="x")  # pyright: ignore[reportAbstractUsage]


@pytest.mark.parametrize(("agency_id", "sink_id"), [("", "x"), (AGENCY, "")])
def test_a_sink_needs_an_agency_and_an_id(agency_id: str, sink_id: str) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        RecordingSink(agency_id=agency_id, sink_id=sink_id)


def test_sink_id_is_what_the_factory_supplied() -> None:
    """F17 writes it into the manifest; no sink class hard-codes its name."""
    assert RecordingSink(sink_id="object_store").sink_id == "object_store"


# --------------------------------------------------------------------------
# upsert_document
# --------------------------------------------------------------------------


def test_the_record_is_written_before_any_chunk() -> None:
    """F3, at the fake: no chunk is retrievable before its document's record."""
    sink = _sink()
    outcome = sink.upsert_document(_record(), _chunks())

    puts = _put_keys(sink.store)
    assert puts[0].endswith("/metadata.json")
    assert all("/chunks/" in key for key in puts[1:])
    assert outcome.published
    assert outcome.chunks_written == 2


def test_re_upserting_identical_input_changes_nothing() -> None:
    """Deterministic ids make every write an upsert."""
    sink = _sink()
    _published(sink)
    before = dict(sink.store.objects)
    _published(sink)
    assert sink.store.objects == before


def test_a_metadata_only_merge_touches_no_chunk() -> None:
    sink = _sink()
    _published(sink)
    sink.store.calls.clear()

    outcome = sink.upsert_document(_record(), None)

    assert outcome.metadata_only
    assert [key.rsplit("/", 1)[1] for key in _put_keys(sink.store)] == ["metadata.json"]


def test_a_metadata_only_merge_is_refused_when_not_declared() -> None:
    """L8: the coordinator must fall back to a full re-push explicitly, never
    have the cheap path silently become an expensive one."""
    sink = _sink(supports_metadata_only_merge=False)
    with pytest.raises(NotSupported, match="supports_metadata_only_merge"):
        sink.upsert_document(_record(), None)
    assert sink.store.calls == []


def test_a_chunk_list_disagreeing_with_the_record_is_refused() -> None:
    with pytest.raises(ValueError, match="chunk_count=3"):
        _sink().upsert_document(_record(chunk_count=3), _chunks(count=2))


def test_a_chunk_from_another_document_is_refused() -> None:
    with pytest.raises(ValueError, match="another document"):
        _sink().upsert_document(_record(), _chunks(document_id="d2"))


def test_chunk_ordinals_must_be_contiguous_from_zero() -> None:
    chunks = _chunks(count=3)
    with pytest.raises(ValueError, match="0..chunk_count-1"):
        _sink().upsert_document(_record(chunk_count=2), [chunks[0], chunks[2]])


class _LyingSink(RecordingSink):
    """Writes nothing and returns whatever outcome it was told to."""

    def __init__(self, outcome: UpsertOutcome) -> None:
        super().__init__(agency_id=AGENCY)
        self.outcome = outcome

    def _upsert_document(
        self, record: DocumentRecord, chunks: tuple[Chunk, ...] | None
    ) -> UpsertOutcome:
        return self.outcome


@pytest.mark.parametrize(
    ("outcome", "chunks", "problem"),
    [
        (
            UpsertOutcome("d1", UpsertStatus.PUBLISHED, 0, metadata_only=False),
            _chunks(),
            "published with 0 of 2",
        ),
        (
            UpsertOutcome("d9", UpsertStatus.PUBLISHED, 2, metadata_only=False),
            _chunks(),
            "names document 'd9'",
        ),
        (
            UpsertOutcome("d1", UpsertStatus.PUBLISHED, 0, metadata_only=True),
            _chunks(),
            "metadata_only=True",
        ),
        (
            UpsertOutcome("d1", UpsertStatus.FAILED_RETRYABLE, 3, metadata_only=False),
            _chunks(),
            "3 of 2",
        ),
        (
            UpsertOutcome("d1", UpsertStatus.PUBLISHED, 0, metadata_only=False),
            None,
            "metadata_only=False",
        ),
    ],
)
def test_an_upsert_outcome_that_misdescribes_the_write_is_not_trusted(
    outcome: UpsertOutcome, chunks: list[Chunk] | None, problem: str
) -> None:
    """Only PUBLISHED enters the manifest (F18); a sink must not vouch for a
    document it did not fully write."""
    with pytest.raises(StoreError, match=problem):
        _LyingSink(outcome).upsert_document(_record(), chunks)


def test_a_partial_failure_may_report_fewer_chunks_written() -> None:
    outcome = UpsertOutcome("d1", UpsertStatus.FAILED_RETRYABLE, 1, metadata_only=False)
    assert _LyingSink(outcome).upsert_document(_record(), _chunks()) == outcome


# --------------------------------------------------------------------------
# Deletes
# --------------------------------------------------------------------------


def test_deleting_a_document_removes_chunks_before_the_record() -> None:
    """F4: nothing may reference content that is already gone."""
    sink = _sink()
    _published(sink)
    sink.store.calls.clear()

    outcome = sink.delete_documents(["d1"])

    deleted = _deleted_keys(sink.store)
    assert deleted[-1].endswith("/metadata.json")
    assert all("/chunks/" in key for key in deleted[:-1])
    assert outcome == DeleteOutcome(deleted=("d1",))
    assert sink.store.objects == {}


def test_deleting_an_absent_document_reports_missing_not_failed() -> None:
    assert _sink().delete_documents(["never"]) == DeleteOutcome(missing=("never",))


def test_a_failed_chunk_delete_keeps_the_record() -> None:
    """F4's real guarantee: while any chunk may remain, so does its record,
    and the document is failed so the journal retries it."""
    sink = _sink()
    _published(sink)
    chunk_key = sink._chunk_key("d1", 1)  # noqa: SLF001
    sink.store.fail_delete_keys.add(chunk_key)

    outcome = sink.delete_documents(["d1"])

    assert set(outcome.failed) == {"d1"}
    assert sink._metadata_key("d1") in sink.store.objects  # noqa: SLF001
    assert chunk_key in sink.store.objects


def test_a_store_that_cannot_delete_defers_the_document() -> None:
    store = InMemoryStore(capabilities=_store_capabilities(can_delete=False))
    sink = RecordingSink(agency_id=AGENCY, store=store)
    _published(sink)

    assert sink.delete_documents(["d1"]) == DeleteOutcome(deferred=("d1",))
    assert len(store.objects) == 3


def test_delete_documents_reports_a_duplicate_id_once() -> None:
    sink = _sink()
    _published(sink)
    assert sink.delete_documents(["d1", "d1"]) == DeleteOutcome(deleted=("d1",))


def test_delete_documents_with_no_ids_does_nothing() -> None:
    sink = _sink()
    assert sink.delete_documents([]) == DeleteOutcome()
    assert sink.store.calls == []


class _ForgetfulSink(RecordingSink):
    """Reports on nothing it was asked to delete."""

    def _delete_documents(self, document_ids: tuple[str, ...]) -> DeleteOutcome[str]:
        return DeleteOutcome()

    def _delete_chunks(
        self, document_id: str, ordinals: tuple[int, ...]
    ) -> DeleteOutcome[int]:
        return DeleteOutcome[int](deleted=(99,))


def test_a_document_the_sink_forgets_to_report_is_recorded_as_failed() -> None:
    """Otherwise it would drop out of the deferred-deletion journal (F7)."""
    outcome = _ForgetfulSink(agency_id=AGENCY).delete_documents(["d1"])
    assert outcome.failed == {"d1": UNREPORTED_DELETE_REASON}


def test_a_sink_reporting_an_unrequested_item_is_not_trusted() -> None:
    with pytest.raises(StoreError, match="not requested"):
        _ForgetfulSink(agency_id=AGENCY).delete_chunks("d1", [3])


def test_a_tail_delete_keeps_every_surviving_chunk() -> None:
    sink = _sink()
    _published(sink, count=4)

    outcome = sink.delete_chunks("d1", [3, 2, 3])

    assert outcome == DeleteOutcome[int](deleted=(2, 3))
    probe = sink.read_document("d1")
    assert probe is not None
    assert probe.chunk_ordinals == (0, 1)


def test_a_failed_chunk_delete_is_reported_by_ordinal() -> None:
    sink = _sink()
    _published(sink, count=3)
    sink.store.fail_delete_keys.add(sink._chunk_key("d1", 2))  # noqa: SLF001

    outcome = sink.delete_chunks("d1", [1, 2])

    assert outcome.deleted == (1,)
    assert set(outcome.failed) == {2}


def test_a_negative_ordinal_is_refused() -> None:
    with pytest.raises(ValueError, match=">= 0"):
        _sink().delete_chunks("d1", [-1])


def test_delete_agency_refuses_another_agency() -> None:
    """A purge aimed at the wrong tenant fails loudly."""
    with pytest.raises(ValueError, match="bound to agency"):
        _sink().delete_agency("someone-else")


def test_delete_agency_is_refused_when_not_declared() -> None:
    with pytest.raises(NotSupported, match="supports_agency_scoped_delete"):
        _sink(supports_agency_scoped_delete=False).delete_agency(AGENCY)


def test_delete_agency_removes_everything_for_the_agency() -> None:
    sink = _sink()
    _published(sink, "d1")
    _published(sink, "d2")
    assert sink.delete_agency(AGENCY) == DeleteOutcome(deleted=(AGENCY,))
    assert sink.store.objects == {}


def test_deleting_an_agency_with_nothing_published_reports_missing() -> None:
    """A replayed purge must not claim to have deleted something again."""
    assert _sink().delete_agency(AGENCY) == DeleteOutcome(missing=(AGENCY,))


def test_deleting_an_agency_on_a_store_that_cannot_delete_is_deferred() -> None:
    store = InMemoryStore(capabilities=_store_capabilities(can_delete=False))
    sink = RecordingSink(agency_id=AGENCY, store=store)
    _published(sink)

    assert sink.delete_agency(AGENCY) == DeleteOutcome(deferred=(AGENCY,))
    assert len(store.objects) == 3


def test_a_pseudo_directory_entry_is_not_read_as_a_chunk() -> None:
    """Parse, never count (D12)."""
    sink = _sink()
    _published(sink)
    prefix = sink._chunk_prefix("d1")  # noqa: SLF001
    sink.store.put_bytes(prefix, b"")

    probe = sink.read_document("d1")

    assert probe is not None
    assert probe.chunk_ordinals == (0, 1)


# --------------------------------------------------------------------------
# sweep_stale and read_document
# --------------------------------------------------------------------------


def test_sweep_stale_refuses_another_agency() -> None:
    with pytest.raises(ValueError, match="bound to agency"):
        _sink().sweep_stale("someone-else", datetime.now(UTC))


def test_sweep_stale_refuses_a_naive_timestamp() -> None:
    """A naive cutoff is ambiguous by hours, and a sweep deletes."""
    with pytest.raises(ValueError, match="timezone-aware"):
        _sink().sweep_stale(AGENCY, datetime(2026, 10, 12))


class _NoOffset(tzinfo):
    """A tzinfo that cannot say what its offset is."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return None


def test_sweep_stale_refuses_a_timestamp_with_no_offset() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _sink().sweep_stale(AGENCY, datetime(2026, 10, 12, tzinfo=_NoOffset()))


def test_sweep_stale_refuses_a_cutoff_in_the_future() -> None:
    """`before` is the run's start; a later one makes every document stale."""
    with pytest.raises(ValueError, match="future"):
        _sink().sweep_stale(AGENCY, datetime.now(UTC) + timedelta(hours=1))


def test_a_sink_without_a_sweep_raises_rather_than_no_ops() -> None:
    with pytest.raises(NotSupported):
        _sink().sweep_stale(AGENCY, datetime.now(UTC))


def test_read_back_is_refused_when_not_declared() -> None:
    """Without it, reconcile must report unverifiable (J9)."""
    with pytest.raises(NotSupported, match="can_read_back"):
        _sink(can_read_back=False).read_document("d1")


def test_read_document_reports_what_the_destination_holds() -> None:
    sink = _sink()
    _published(sink, count=3)
    assert sink.read_document("d1") == DocumentProbe(
        "d1", record_present=True, chunk_count=3, chunk_ordinals=(0, 1, 2)
    )
    assert sink.read_document("absent") is None


# --------------------------------------------------------------------------
# The factory (A13) and the layering
# --------------------------------------------------------------------------


def test_the_factory_names_the_task_that_builds_the_llm_module_sink() -> None:
    """Until L1 lands, a request for that sink is a clear refusal."""
    settings = Settings.model_construct(content_sink="llm_module")
    with pytest.raises(ConfigurationError, match="task L1"):
        build_sink(settings, agency_id=AGENCY)


def test_the_factory_builds_the_object_store_sink() -> None:
    """Building it makes no network call: boto3 connects on first request."""
    settings = Settings.model_construct(
        content_sink="object_store",
        content_external_store_backend="s3",
        content_external_prefix="content",
        external_s3_endpoint_url="http://store.invalid:9000",
        external_s3_bucket_name="agency-content",
        external_s3_access_key_id=SecretStr("key"),
        external_s3_secret_access_key=SecretStr("secret"),
        external_s3_region="us-east-1",
        external_s3_supports_batch_delete=True,
        export_concurrency=4,
    )

    sink = build_sink(settings, agency_id=AGENCY)

    assert isinstance(sink, ObjectStoreSink)
    assert sink.sink_id == "object_store"
    assert sink.agency_id == AGENCY
    assert isinstance(sink.store, S3Store)


def test_the_object_store_sink_refuses_an_unconfigured_store() -> None:
    settings = Settings.model_construct(
        content_sink="object_store",
        content_external_store_backend="s3",
        content_external_prefix="content",
        external_s3_endpoint_url="",
        external_s3_bucket_name="",
        external_s3_access_key_id=SecretStr(""),
        external_s3_secret_access_key=SecretStr(""),
        external_s3_region="us-east-1",
        external_s3_supports_batch_delete=True,
        export_concurrency=4,
    )
    with pytest.raises(ConfigurationError, match="EXTERNAL_S3_ENDPOINT_URL"):
        build_sink(settings, agency_id=AGENCY)


def _imports(relative: str) -> set[str]:
    tree = ast.parse((EXPORTER_ROOT / relative).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_store_layer_does_not_import_the_sink_layer() -> None:
    """Layering runs sinks -> stores -> core, never upward."""
    assert not any(
        name.startswith("exporter.sinks") for name in _imports("stores/base.py")
    )


@pytest.mark.parametrize(
    "relative", ["sinks/base.py", "stores/base.py", "sinks/object_store_sink.py"]
)
def test_the_interfaces_import_no_transport(relative: str) -> None:
    """The ABCs and the object-store sink are transport-free; boto3 lives in
    the s3 backend only, requests arrives in L."""
    roots = {name.split(".")[0] for name in _imports(relative)}
    assert not roots & {"boto3", "botocore", "azure", "requests", "httpx"}


def test_boto3_is_imported_only_by_the_s3_backend_and_ckb_reader() -> None:
    """Each bucket has one transport module; the coordinator imports neither."""
    importers = {
        path.relative_to(EXPORTER_ROOT).as_posix()
        for path in EXPORTER_ROOT.rglob("*.py")
        if {
            name.split(".")[0]
            for name in _imports(path.relative_to(EXPORTER_ROOT).as_posix())
        }
        & {"boto3", "botocore"}
    }
    assert importers == {
        "stores/s3_store.py",
        # CKB's own read-only bucket: README rule 6 requires our own boto3
        # client, GetObject only.
        "services/ckb_bucket.py",
    }


def test_the_app_does_not_build_a_sink_yet() -> None:
    """Stage F wires the factory; until then startup must not touch it."""
    assert not any(name.startswith("exporter.sinks") for name in _imports("api/app.py"))
