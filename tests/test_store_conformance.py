"""D13 — the store conformance suite: one set of assertions, any store.

This is what makes "does SeaweedFS work?" a fifteen-minute question. The
in-memory fake always runs; the live variants run against whatever
S3-compatible endpoint CONFORMANCE_S3_* names, and skip when it is unset:

    docker run -d -p 9000:9000 minio/minio server /data
    #   or: docker run -d -p 8333:8333 chrislusf/seaweedfs server -s3
    # create a bucket, then:
    export CONFORMANCE_S3_ENDPOINT_URL=http://localhost:9000
    export CONFORMANCE_S3_BUCKET=conformance
    export CONFORMANCE_S3_ACCESS_KEY_ID=...
    export CONFORMANCE_S3_SECRET_ACCESS_KEY=...
    pytest tests/test_store_conformance.py tests/test_sink_conformance.py -v

`s3-batch` and `s3-per-key` are the same endpoint with and without
DeleteObjects (D11), so one run proves both delete paths report the same
outcome. Every test writes under its own `conformance/<uuid>/` prefix and
empties it afterwards.

A store passes when every variant here is green. Anything this suite does not
assert is something the pipeline must not depend on (D10).
"""

import json
import uuid
from collections.abc import Callable, Iterator

import pytest
from exporter_fakes import InMemoryStore, empty_prefix, live_s3_store_or_skip

from exporter.stores.base import ExternalStoreClient, NotSupported
from exporter.stores.local_store import LocalFileStore

# A page size small enough that five keys need three pages, so pagination is
# exercised on every endpoint rather than only on a large corpus.
_PAGE_SIZE = 2

_VARIANTS: dict[str, Callable[[pytest.FixtureRequest], ExternalStoreClient]] = {
    "in-memory": lambda _request: InMemoryStore(),
    # D19's manifest-store backend. Development only, but it still has to
    # meet every rule a store meets, or a manifest that works locally would
    # behave differently from the same manifest on S3.
    "local": lambda request: LocalFileStore(
        request.getfixturevalue("tmp_path") / "store", allow_local=True
    ),
    "s3-batch": lambda _request: live_s3_store_or_skip(
        supports_batch_delete=True, list_page_size=_PAGE_SIZE
    ),
    "s3-per-key": lambda _request: live_s3_store_or_skip(
        supports_batch_delete=False, list_page_size=_PAGE_SIZE
    ),
}


@pytest.fixture(params=sorted(_VARIANTS))
def store(request: pytest.FixtureRequest) -> ExternalStoreClient:
    return _VARIANTS[request.param](request)


@pytest.fixture
def prefix(store: ExternalStoreClient) -> Iterator[str]:
    """A prefix no other test or run shares, emptied afterwards."""
    value = f"conformance/{uuid.uuid4().hex}/"
    yield value
    empty_prefix(store, value)


def test_a_nested_key_round_trips_byte_identically(
    store: ExternalStoreClient, prefix: str
) -> None:
    key = f"{prefix}agencies/a1/documents/d1/chunks/00000.json"
    value = {"text": "Õppetoetuse taotlus — 3. detsember", "ordinal": 0}

    stored = store.put_json(key, value)

    assert stored.key == key
    payload = store.get_bytes(key)
    assert payload is not None
    assert json.loads(payload) == value
    assert "Õppetoetuse".encode() in payload  # ensure_ascii=False survived
    assert store.exists(key)


def test_an_overwrite_replaces_the_object(
    store: ExternalStoreClient, prefix: str
) -> None:
    key = f"{prefix}manifest.json"
    store.put_bytes(key, b"first")
    store.put_bytes(key, b"second")
    assert store.get_bytes(key) == b"second"


def test_a_missing_key_is_absent_not_an_error(
    store: ExternalStoreClient, prefix: str
) -> None:
    key = f"{prefix}never-written.json"
    assert store.get_bytes(key) is None
    assert store.exists(key) is False


def test_a_delete_reports_deleted_and_missing(
    store: ExternalStoreClient, prefix: str
) -> None:
    present = [f"{prefix}a.json", f"{prefix}b.json"]
    absent = f"{prefix}never-written.json"
    for key in present:
        store.put_bytes(key, b"{}")

    outcome = store.delete_many([*present, absent])

    assert sorted(outcome.deleted) == sorted(present)
    assert outcome.missing == (absent,)
    assert outcome.is_complete
    for key in present:
        assert store.get_bytes(key) is None


def test_a_replayed_delete_is_all_missing(
    store: ExternalStoreClient, prefix: str
) -> None:
    """Idempotency: the second attempt must not fail because the first worked."""
    key = f"{prefix}a.json"
    store.put_bytes(key, b"{}")
    store.delete_many([key])

    outcome = store.delete_many([key])

    assert outcome.missing == (key,)
    assert not outcome.deleted


def test_a_listing_paginates_past_the_page_size(
    store: ExternalStoreClient, prefix: str
) -> None:
    keys = [f"{prefix}chunks/{ordinal:05d}.json" for ordinal in range(5)]
    for key in keys:
        store.put_bytes(key, b"{}")

    listed = [obj.key for obj in store.list_keys(f"{prefix}chunks/")]

    # Parsed by exact key, never counted (D12): a gateway may add
    # pseudo-directory entries, and those must not hide a missing key.
    assert sorted(key for key in listed if key in keys) == keys
    assert len(set(listed)) == len(listed)


def test_a_listing_stays_inside_its_prefix(
    store: ExternalStoreClient, prefix: str
) -> None:
    """`documents/d1/` must never return `documents/d10/`, or a document
    delete would take its neighbour with it.

    The backend's own hook is called, not `list_keys`: the public verb
    refuses an out-of-prefix key before a test could see it, so asserting
    through it would prove the base class and not the store."""
    store.put_bytes(f"{prefix}documents/d1/metadata.json", b"{}")
    store.put_bytes(f"{prefix}documents/d10/metadata.json", b"{}")

    listed = [obj.key for obj in store._list_keys(f"{prefix}documents/d1/")]

    assert f"{prefix}documents/d1/metadata.json" in listed
    assert f"{prefix}documents/d10/metadata.json" not in listed
    assert all(key.startswith(f"{prefix}documents/d1/") for key in listed)


def test_a_conditional_write_is_not_supported(
    store: ExternalStoreClient, prefix: str
) -> None:
    with pytest.raises(NotSupported):
        store.put_bytes(f"{prefix}a.json", b"{}", overwrite=False)


def test_a_store_declares_every_capability_the_pipeline_needs(
    store: ExternalStoreClient,
) -> None:
    capabilities = store.capabilities
    assert capabilities.can_read
    assert capabilities.can_list
    assert capabilities.can_delete
