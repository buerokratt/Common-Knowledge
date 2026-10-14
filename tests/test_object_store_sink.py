"""D14 — ObjectStoreSink behaviour the shared conformance suite cannot reach.

tests/test_sink_conformance.py asserts the ContentSink contract against every
sink. This file covers what is specific to the object-store sink: how it
rolls per-key outcomes up to one answer per document, how it reports a store
that fails part-way through a delete, and that its prefix obeys rule 1.
"""

from collections.abc import Iterator

import pytest
from exporter_fakes import InMemoryStore

from exporter.core.ids import make_chunk_id
from exporter.core.schemas import Chunk, DocumentRecord, TextSpan
from exporter.sinks.base import DeleteOutcome, StoreError
from exporter.sinks.object_store_sink import (
    NOT_ATTEMPTED_REASON,
    ObjectStoreSink,
    roll_up,
)
from exporter.stores.base import StoreObject

AGENCY = "agency-1"


# --------------------------------------------------------------------------
# roll_up: one answer per document
# --------------------------------------------------------------------------


def test_a_failure_outranks_everything_and_keeps_its_reasons() -> None:
    outcome = DeleteOutcome(
        deleted=("k1",),
        missing=("k2",),
        deferred=("k3",),
        failed={"k4": "AccessDenied", "k5": "InternalError", "k6": "AccessDenied"},
    )
    assert roll_up("d1", outcome) == DeleteOutcome(
        failed={"d1": "delete-incomplete: AccessDenied, InternalError"}
    )


def test_a_rolled_up_failure_carries_a_bounded_number_of_reasons() -> None:
    outcome = DeleteOutcome(failed={f"k{n}": f"Code{n}" for n in range(10)})
    reason = roll_up("d1", outcome).failed["d1"]
    assert reason.count("Code") == 3


def test_deferred_outranks_deleted() -> None:
    outcome = DeleteOutcome(deleted=("k1",), deferred=("k2",))
    assert roll_up("d1", outcome) == DeleteOutcome(deferred=("d1",))


def test_deleted_outranks_missing() -> None:
    outcome = DeleteOutcome(deleted=("k1",), missing=("k2",))
    assert roll_up("d1", outcome) == DeleteOutcome(deleted=("d1",))


@pytest.mark.parametrize(
    "outcome", [DeleteOutcome(missing=("k1", "k2")), DeleteOutcome()]
)
def test_missing_only_when_nothing_was_there(outcome: DeleteOutcome[str]) -> None:
    assert roll_up("d1", outcome) == DeleteOutcome(missing=("d1",))


# --------------------------------------------------------------------------
# A store that fails part-way through a delete
# --------------------------------------------------------------------------


class _FlakyStore(InMemoryStore):
    """Raises a whole-call StoreError — the kind delete_many may raise —
    when listing a matching prefix or deleting a matching key."""

    def __init__(
        self, *, fail_list_containing: str = "", fail_delete_containing: str = ""
    ) -> None:
        super().__init__()
        self.fail_list_containing = fail_list_containing
        self.fail_delete_containing = fail_delete_containing

    def _list_keys(self, prefix: str) -> Iterator[StoreObject]:
        if self.fail_list_containing and self.fail_list_containing in prefix:
            raise StoreError("InjectedOutage (HTTP 503)")
        return super()._list_keys(prefix)

    def _delete_many(self, keys: tuple[str, ...]) -> DeleteOutcome[str]:
        if self.fail_delete_containing and any(
            self.fail_delete_containing in key for key in keys
        ):
            raise StoreError("InjectedOutage (HTTP 503)")
        return super()._delete_many(keys)


def _sink(store: InMemoryStore, prefix: str = "content") -> ObjectStoreSink:
    return ObjectStoreSink(
        store=store, prefix=prefix, agency_id=AGENCY, sink_id="object_store"
    )


def _publish(sink: ObjectStoreSink, document_id: str, count: int = 2) -> None:
    record = DocumentRecord(
        document_id=document_id,
        source_base_id="s1",
        content_origin="cleaned",
        source_url="https://www.eesti.ee/teenus",
        source={"title": "Õppetoetus"},
        raw_sha256="r" * 64,
        content_sha256="c" * 64,
        metadata_sha256="m" * 64,
        chunk_count=count,
        synced_at="2026-10-01T00:00:00Z",
    )
    chunks = [
        Chunk(
            chunk_id=make_chunk_id(AGENCY, document_id, ordinal),
            document_id=document_id,
            ordinal=ordinal,
            text=f"lõik {ordinal}",
            span=TextSpan(ordinal * 10, ordinal * 10 + 6),
        )
        for ordinal in range(count)
    ]
    assert sink.upsert_document(record, chunks).published


def test_a_store_failure_keeps_earlier_outcomes_and_fails_the_rest() -> None:
    """Raising would hide that d1 is already gone; the journal needs to know
    exactly which documents to retry."""
    store = _FlakyStore(fail_list_containing="/documents/d2/")
    sink = _sink(store)
    for document_id in ("d1", "d2", "d3"):
        _publish(sink, document_id)

    outcome = sink.delete_documents(["d1", "d2", "d3"])

    assert outcome.deleted == ("d1",)
    assert set(outcome.failed) == {"d2", "d3"}
    assert "InjectedOutage" in outcome.failed["d2"]
    assert outcome.failed["d3"] == NOT_ATTEMPTED_REASON
    # d3 was never touched, so it is still whole.
    probe = sink.read_document("d3")
    assert probe is not None
    assert probe.record_present
    assert probe.chunk_count == 2


def test_a_record_delete_that_fails_after_the_contents_reports_the_agency_failed() -> (
    None
):
    store = _FlakyStore(fail_delete_containing="metadata.json")
    sink = _sink(store)
    _publish(sink, "d1")

    outcome = sink.delete_agency(AGENCY)

    assert set(outcome.failed) == {AGENCY}
    probe = sink.read_document("d1")
    assert probe is not None
    assert probe.record_present
    assert probe.chunk_count == 0


def test_a_pseudo_directory_entry_is_not_deleted_as_an_object() -> None:
    """A gateway's rendering of a prefix is not an object; sending it to a
    delete would cost a request and, on some gateways, an error that holds
    the record back forever."""
    store = InMemoryStore()
    sink = _sink(store)
    _publish(sink, "d1")
    pseudo = "content/agencies/agency-1/documents/d1/chunks/"
    store.put_bytes(pseudo, b"")
    store.calls.clear()

    assert sink.delete_documents(["d1"]) == DeleteOutcome(deleted=("d1",))
    deleted: list[object] = []
    for verb, target in store.calls:
        if verb == "delete":
            assert isinstance(target, tuple)
            deleted.extend(target)
    assert pseudo not in deleted
    assert store.get_bytes(pseudo) == b""


# --------------------------------------------------------------------------
# Rule 1
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prefix", ["scrapped-data", "uploads/scrapped-data", "content/Scrapped-Data"]
)
def test_the_sink_refuses_a_prefix_inside_the_scraped_tree(prefix: str) -> None:
    with pytest.raises(ValueError, match="rule 1"):
        _sink(InMemoryStore(), prefix)
