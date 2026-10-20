"""D1–D5 — the ExternalStoreClient contract, DeleteOutcome and put_json.

Pure: an in-memory fake store, no Docker, no network, no boto3. These pin the
rules the base class enforces for every backend; D9 adds the S3-backend cases
to this file, and the store conformance suite (D13) re-asserts the backend-
facing half against a real endpoint.
"""

import json
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from exporter_fakes import INJECTED_FAILURE, InMemoryStore

from exporter.core.schemas import (
    DocumentRecord,
    Manifest,
    ManifestDocumentEntry,
    to_json_dict,
)
from exporter.stores.base import (
    JSON_CONTENT_TYPE,
    UNREPORTED_DELETE_REASON,
    DeleteOutcome,
    ExternalStoreClient,
    NotSupported,
    StoreCapabilities,
    StoreError,
    StoreObject,
    validate_object_metadata,
)


def _store(**capabilities: bool) -> InMemoryStore:
    values = {
        "can_delete": True,
        "can_list": True,
        "can_read": True,
        "supports_batch_delete": True,
    }
    return InMemoryStore(capabilities=StoreCapabilities(**(values | capabilities)))


def _record(source: Mapping[str, Any]) -> DocumentRecord:
    return DocumentRecord(
        document_id="d1",
        source_base_id="s1",
        content_origin="cleaned",
        source_url="https://www.eesti.ee/teenus",
        source=source,
        raw_sha256="r",
        content_sha256="c",
        metadata_sha256="m",
        chunk_count=1,
        synced_at="2026-10-12T00:00:00Z",
    )


# --------------------------------------------------------------------------
# DeleteOutcome — D3
# --------------------------------------------------------------------------


def test_an_item_in_two_categories_is_refused() -> None:
    """Deleted-and-failed is two answers to one question; the journal could
    not tell which is true."""
    with pytest.raises(ValueError, match="exactly one category"):
        DeleteOutcome(deleted=("a",), failed={"a": "AccessDenied"})


def test_an_item_repeated_within_one_category_is_refused() -> None:
    with pytest.raises(ValueError, match="'a'"):
        DeleteOutcome(missing=("a", "a"))


def test_already_gone_counts_as_success() -> None:
    """The point of D3: a replayed delete must not fail because the first
    attempt worked."""
    outcome = DeleteOutcome(deleted=("a",), missing=("b",))
    assert outcome.succeeded == ("a", "b")
    assert outcome.is_complete


def test_deferred_and_failed_are_what_the_journal_keeps() -> None:
    outcome = DeleteOutcome(deleted=("a",), deferred=("b",), failed={"c": "SlowDown"})
    assert outcome.unresolved == ("b", "c")
    assert not outcome.is_complete
    assert outcome.counts() == {"deleted": 1, "missing": 0, "deferred": 1, "failed": 1}


def test_combine_merges_batches() -> None:
    merged = DeleteOutcome.combine(
        DeleteOutcome(deleted=("a",)),
        DeleteOutcome(missing=("b",), failed={"c": "InternalError"}),
    )
    assert merged == DeleteOutcome(
        deleted=("a",), missing=("b",), failed={"c": "InternalError"}
    )


def test_combine_refuses_an_item_reported_by_two_batches() -> None:
    """Two batches reporting one key means it was sent twice — a bug upstream."""
    with pytest.raises(ValueError, match="exactly one category"):
        DeleteOutcome.combine(
            DeleteOutcome(deleted=("a",)), DeleteOutcome(missing=("a",))
        )


def test_combine_refuses_an_item_failed_by_two_batches() -> None:
    """A dict merge would keep the later reason and hide the double send."""
    with pytest.raises(ValueError, match="exactly one category"):
        DeleteOutcome.combine(
            DeleteOutcome(failed={"a": "SlowDown"}),
            DeleteOutcome(failed={"a": "InternalError"}),
        )


def test_failed_cannot_be_mutated_in_place() -> None:
    outcome = DeleteOutcome(failed={"a": "AccessDenied"})
    with pytest.raises(TypeError):
        outcome.failed["b"] = "x"  # pyright: ignore[reportIndexIssue]


def test_failure_reasons_are_sanitised() -> None:
    """A reason is logged and journalled, so a URL with userinfo in an SDK
    message must not carry its credential along."""
    outcome = DeleteOutcome(failed={"a": "GET https://user:pw@store.example/x"})
    assert "pw" not in outcome.failed["a"]
    assert "[redacted]" in outcome.failed["a"]


def test_deferring_defers_every_item_once() -> None:
    assert DeleteOutcome.deferring(["a", "b", "a"]) == DeleteOutcome(
        deferred=("a", "b")
    )


def test_ordinals_work_as_items() -> None:
    """Generic in the item type: delete_chunks reports ordinals, not keys."""
    outcome = DeleteOutcome[int](deleted=(3,), missing=(4,), failed={5: "x"})
    assert outcome.unresolved == (5,)


def test_an_outcome_is_journal_serialisable() -> None:
    """F7 writes outcomes as JSON lines through to_json_dict."""
    outcome = DeleteOutcome(deleted=("a",), deferred=("b",), failed={"c": "x"})
    assert json.loads(json.dumps(to_json_dict(outcome))) == {
        "deleted": ["a"],
        "missing": [],
        "deferred": ["b"],
        "failed": {"c": "x"},
    }


# --------------------------------------------------------------------------
# delete_many — returns, never raises, for per-key failures (D3, D9)
# --------------------------------------------------------------------------


def test_delete_many_returns_a_per_key_failure_instead_of_raising() -> None:
    store = InMemoryStore(fail_delete_keys={"k2"})
    store.put_bytes("k1", b"1")
    store.put_bytes("k2", b"2")

    outcome = store.delete_many(["k1", "k2"])

    assert outcome.deleted == ("k1",)
    assert outcome.failed == {"k2": INJECTED_FAILURE}


def test_delete_many_reports_an_absent_key_as_missing() -> None:
    assert InMemoryStore().delete_many(["gone"]) == DeleteOutcome(missing=("gone",))


def test_delete_many_with_no_keys_makes_no_backend_call() -> None:
    store = InMemoryStore()
    assert store.delete_many([]) == DeleteOutcome()
    assert store.calls == []


def test_delete_many_reports_a_duplicate_key_once() -> None:
    store = InMemoryStore()
    store.put_bytes("k", b"1")
    assert store.delete_many(["k", "k"]) == DeleteOutcome(deleted=("k",))
    assert store.calls[-1] == ("delete", ("k",))


def test_a_store_that_cannot_delete_defers_rather_than_failing_mid_run() -> None:
    store = _store(can_delete=False)
    store.put_bytes("k", b"1")

    outcome = store.delete_many(["k"])

    assert outcome == DeleteOutcome(deferred=("k",))
    assert "k" in store.objects
    assert not any(verb == "delete" for verb, _ in store.calls)


def test_a_key_the_backend_forgets_to_report_is_recorded_as_failed() -> None:
    """Otherwise it would vanish from the outcome and never be journalled."""
    store = InMemoryStore(unreported_delete_keys={"lost"})
    outcome = store.delete_many(["kept", "lost"])
    assert outcome.missing == ("kept",)
    assert outcome.failed == {"lost": UNREPORTED_DELETE_REASON}


def test_a_backend_reporting_an_unrequested_key_is_not_trusted() -> None:
    class Confused(InMemoryStore):
        def _delete_many(self, keys: tuple[str, ...]) -> DeleteOutcome[str]:
            return DeleteOutcome(deleted=(*keys, "someone-elses"))

    with pytest.raises(StoreError, match="not requested"):
        Confused().delete_many(["k"])


# --------------------------------------------------------------------------
# Absent versus error (D4)
# --------------------------------------------------------------------------


def test_get_bytes_returns_none_only_for_an_absent_key() -> None:
    """None is a genuine first run (C9)."""
    assert InMemoryStore().get_bytes("manifest.json") is None


def test_get_bytes_raises_for_a_store_error_and_never_returns_none() -> None:
    """An outage that read as absence would republish the corpus and orphan
    every deletion made in the meantime."""
    store = InMemoryStore(fail_keys={"manifest.json"})
    with pytest.raises(StoreError):
        store.get_bytes("manifest.json")


def test_exists_follows_the_same_absent_versus_error_rule() -> None:
    store = InMemoryStore(fail_keys={"broken"})
    store.put_bytes("present", b"1")
    assert store.exists("present") is True
    assert store.exists("absent") is False
    with pytest.raises(StoreError):
        store.exists("broken")


def test_reading_from_a_store_that_cannot_read_is_not_supported() -> None:
    with pytest.raises(NotSupported, match="can_read"):
        _store(can_read=False).get_bytes("k")


def test_not_supported_is_a_store_error() -> None:
    """Callers that fail closed on StoreError also fail closed on a missing
    capability."""
    assert issubclass(NotSupported, StoreError)


# --------------------------------------------------------------------------
# put_json — deterministic, UTF-8 (D5)
# --------------------------------------------------------------------------


def test_put_json_keeps_estonian_readable() -> None:
    store = InMemoryStore()
    store.put_json("k", {"title": "õäöü šž"})
    payload = store.objects["k"].payload
    assert "õäöü šž".encode() in payload
    assert b"\\u" not in payload
    assert store.objects["k"].content_type == JSON_CONTENT_TYPE


def test_put_json_bytes_do_not_depend_on_key_order() -> None:
    store = InMemoryStore()
    store.put_json("a", {"b": 1, "a": {"y": 2, "x": 3}})
    store.put_json("b", {"a": {"x": 3, "y": 2}, "b": 1})
    assert store.objects["a"].payload == store.objects["b"].payload


def test_an_unchanged_value_re_serialises_byte_identically() -> None:
    """Verification 20 compares manifest bytes across runs."""
    store = InMemoryStore()
    value = {"documents": {"d2": 2, "d1": 1}, "agency_id": "a1"}
    store.put_json("k", value)
    first = store.objects["k"].payload
    store.put_json("k", value)
    assert store.objects["k"].payload == first


def test_put_json_serialises_the_frozen_schemas() -> None:
    """DocumentRecord.source and Manifest.documents are MappingProxyType,
    which json.dumps alone cannot serialise."""
    store = InMemoryStore()
    entry = ManifestDocumentEntry(
        source_base_id="s1",
        content_origin="cleaned",
        source_updated_at="2026-10-12T00:00:00Z",
        source_status="finished",
        raw_sha256="r",
        content_sha256="c",
        metadata_sha256="m",
        file_size=None,
        chunk_count=1,
        chunker_fingerprint="f",
        state="published",
        processed_at="2026-10-12T00:00:00Z",
    )
    manifest = Manifest(
        schema_version=1,
        manifest_schema_version=2,
        agency_id="a1",
        sink_id="object_store",
        corpus_watermark=None,
        document_count=1,
        chunker_fingerprint="f",
        committed_at="2026-10-12T00:00:00Z",
        documents={"d1": entry},
    )

    store.put_json("record", _record({"title": "Taotlus", "tags": ["a"]}))
    store.put_json("manifest", manifest)

    assert json.loads(store.objects["record"].payload)["source"]["title"] == "Taotlus"
    loaded = json.loads(store.objects["manifest"].payload)
    assert loaded["documents"]["d1"]["chunk_count"] == 1


def test_put_json_refuses_nan() -> None:
    """NaN is not JSON; a consumer's parser would reject the object."""
    with pytest.raises(ValueError):
        InMemoryStore().put_json("k", {"x": float("nan")})


# --------------------------------------------------------------------------
# Object metadata is ASCII only (C6)
# --------------------------------------------------------------------------


def test_non_ascii_metadata_is_refused_before_anything_is_stored() -> None:
    """Metadata travels as HTTP headers; an Estonian title would 400 or be
    mangled. Refused here, before the transport sees it."""
    store = InMemoryStore()
    with pytest.raises(ValueError, match="'title'") as raised:
        store.put_bytes("k", b"1", metadata={"title": "Õppetoetus"})
    assert "Õppetoetus" not in str(raised.value)
    assert store.objects == {}
    assert store.calls == []


def test_ascii_metadata_passes_through() -> None:
    store = InMemoryStore()
    store.put_bytes("k", b"1", metadata={"document-id": "d1", "sha256": "ab12"})
    assert store.objects["k"].metadata == {"document-id": "d1", "sha256": "ab12"}


def test_metadata_values_must_be_strings() -> None:
    with pytest.raises(ValueError, match="str to str"):
        validate_object_metadata({"count": 3})  # pyright: ignore[reportArgumentType]


# --------------------------------------------------------------------------
# put_bytes, list_keys and the abstract surface
# --------------------------------------------------------------------------


def test_put_bytes_returns_what_was_stored() -> None:
    stored = InMemoryStore().put_bytes("a/b/c.json", b"12345")
    assert stored.key == "a/b/c.json"
    assert stored.size_bytes == 5


def test_a_conditional_write_is_not_supported() -> None:
    """D10: no conditional writes anywhere; the run lock gives exclusion."""
    with pytest.raises(NotSupported, match="conditional"):
        InMemoryStore().put_bytes("k", b"1", overwrite=False)


def test_put_bytes_refuses_text() -> None:
    with pytest.raises(TypeError, match="put_json"):
        InMemoryStore().put_bytes("k", "text")  # pyright: ignore[reportArgumentType]


def test_an_empty_key_is_refused() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        InMemoryStore().get_bytes("")


def test_listing_without_a_prefix_is_refused() -> None:
    """Listing a whole bucket is never what a sweep means."""
    with pytest.raises(ValueError, match="non-empty prefix"):
        list(InMemoryStore().list_keys(""))


def test_listing_on_a_store_that_cannot_list_is_not_supported() -> None:
    with pytest.raises(NotSupported, match="can_list"):
        _store(can_list=False).list_keys("content/")


def test_list_keys_returns_only_the_prefix() -> None:
    store = InMemoryStore()
    for key in ("content/a/1", "content/a/2", "content/b/1"):
        store.put_bytes(key, b"x")
    assert [obj.key for obj in store.list_keys("content/a/")] == [
        "content/a/1",
        "content/a/2",
    ]


class _PrefixIgnoringStore(InMemoryStore):
    """A gateway that ignores the prefix and lists the whole bucket."""

    def _list_keys(self, prefix: str) -> Iterator[StoreObject]:
        for key, stored in sorted(self.objects.items()):
            yield StoreObject(key=key, size_bytes=len(stored.payload))


def test_a_listing_outside_the_prefix_is_not_trusted() -> None:
    store = _PrefixIgnoringStore()
    store.put_bytes("content/agencies/a1/x", b"1")
    store.put_bytes("content/agencies/a2/y", b"2")
    with pytest.raises(StoreError, match="outside the requested prefix"):
        list(store.list_keys("content/agencies/a1/"))


def test_a_prefix_delete_over_a_bad_listing_deletes_nothing() -> None:
    """delete_many drains its input before calling the backend, so the
    foreign key aborts the delete before any key is touched."""
    store = _PrefixIgnoringStore()
    store.put_bytes("content/agencies/a1/x", b"1")
    store.put_bytes("content/agencies/a2/y", b"2")
    with pytest.raises(StoreError):
        store.delete_many(obj.key for obj in store.list_keys("content/agencies/a1/"))
    assert len(store.objects) == 2
    assert not any(verb == "delete" for verb, _ in store.calls)


def test_a_backend_missing_a_hook_cannot_be_instantiated() -> None:
    class Incomplete(ExternalStoreClient):
        @property
        def capabilities(self) -> StoreCapabilities:
            return StoreCapabilities(True, True, True, True)

        def _get_bytes(self, key: str) -> bytes | None:
            return None

        def _list_keys(self, prefix: str) -> Iterator[StoreObject]:
            return iter(())

    with pytest.raises(TypeError, match="abstract"):
        Incomplete()  # pyright: ignore[reportAbstractUsage]
