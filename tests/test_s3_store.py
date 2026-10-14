"""D6, D9, D11 — the s3 backend against stubbed S3 responses.

No network and no Docker: botocore's own Stubber answers every call, from the
botocore already pinned for this service, so there is no new dependency. The
same behaviour against a real endpoint is the store conformance suite's job
(tests/test_store_conformance.py); this file pins the mapping from S3's
answers to the store contract, which a real endpoint cannot be made to
produce on demand — a 403 that must not read as absence, a batch delete that
half fails, a 500.
"""

import io
from collections.abc import Iterator

import pytest
from botocore.exceptions import EndpointConnectionError
from botocore.response import StreamingBody
from botocore.stub import Stubber
from pydantic import SecretStr

from exporter.api.config import ConfigurationError, Settings
from exporter.stores.base import DeleteOutcome, StoreError
from exporter.stores.factory import (
    StoreConnection,
    build_destination_store,
    build_store,
)
from exporter.stores.s3_store import S3Store

BUCKET = "agency-content"


def _store(
    *, supports_batch_delete: bool = True, list_page_size: int = 1000
) -> S3Store:
    return S3Store(
        endpoint_url="http://store.invalid:9000",
        bucket=BUCKET,
        access_key_id="external-key",
        secret_access_key="external-secret",
        region="us-east-1",
        supports_batch_delete=supports_batch_delete,
        list_page_size=list_page_size,
    )


@pytest.fixture
def store() -> S3Store:
    return _store()


@pytest.fixture
def stub(store: S3Store) -> Iterator[Stubber]:
    with Stubber(store._client) as stubber:
        yield stubber
        stubber.assert_no_pending_responses()


def _head_ok(stubber: Stubber, key: str) -> None:
    stubber.add_response("head_object", {}, {"Bucket": BUCKET, "Key": key})


def _head_missing(stubber: Stubber, key: str) -> None:
    stubber.add_client_error(
        "head_object",
        service_error_code="404",
        http_status_code=404,
        expected_params={"Bucket": BUCKET, "Key": key},
    )


# --------------------------------------------------------------------------
# Reads: absent versus error (D4)
# --------------------------------------------------------------------------


def test_get_bytes_returns_the_body(store: S3Store, stub: Stubber) -> None:
    body = "õpe".encode()
    stub.add_response(
        "get_object",
        {"Body": StreamingBody(io.BytesIO(body), len(body))},
        {"Bucket": BUCKET, "Key": "a/b.json"},
    )
    assert store.get_bytes("a/b.json") == body


def test_a_missing_key_reads_as_none(store: S3Store, stub: Stubber) -> None:
    stub.add_client_error("get_object", "NoSuchKey", http_status_code=404)
    assert store.get_bytes("a/b.json") is None


def test_a_403_is_an_error_and_never_absence(store: S3Store, stub: Stubber) -> None:
    """S3 answers 403 for a missing key when ListBucket is not granted. Reading
    that as absence would turn a permissions fault into a silent first run."""
    stub.add_client_error("get_object", "AccessDenied", http_status_code=403)
    with pytest.raises(StoreError) as excinfo:
        store.get_bytes("a/b.json")
    assert excinfo.value.permanent


@pytest.mark.parametrize("code", ["404", "NotFound"])
def test_a_bare_404_on_get_is_an_error_and_never_absence(
    store: S3Store, stub: Stubber, code: str
) -> None:
    """A proxy in front of a mistyped endpoint answers 404 with no S3 body.
    Only S3's own NoSuchKey may read as a missing manifest."""
    stub.add_client_error("get_object", code, http_status_code=404)
    with pytest.raises(StoreError):
        store.get_bytes("a/manifest.json")


def test_a_missing_bucket_is_an_error_and_never_absence(
    store: S3Store, stub: Stubber
) -> None:
    stub.add_client_error("get_object", "NoSuchBucket", http_status_code=404)
    with pytest.raises(StoreError, match="NoSuchBucket"):
        store.get_bytes("a/b.json")


def test_a_server_error_is_retryable(store: S3Store, stub: Stubber) -> None:
    stub.add_client_error("get_object", "InternalError", http_status_code=500)
    with pytest.raises(StoreError) as excinfo:
        store.get_bytes("a/b.json")
    assert not excinfo.value.permanent


def test_a_throttle_is_retryable(store: S3Store, stub: Stubber) -> None:
    stub.add_client_error("get_object", "SlowDown", http_status_code=503)
    with pytest.raises(StoreError) as excinfo:
        store.get_bytes("a/b.json")
    assert not excinfo.value.permanent


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("RequestLimitExceeded", 400),
        ("BandwidthLimitExceeded", 400),
        ("TooManyRequestsException", 400),
        ("OperationAborted", 409),
        ("RequestTimeTooSkewed", 403),
        ("BadDigest", 400),
        ("RequestTimeout", 400),
    ],
)
def test_a_transient_4xx_is_retryable(
    store: S3Store, stub: Stubber, code: str, status: int
) -> None:
    """A throttle classed as permanent would trip F19's abort on a busy store."""
    stub.add_client_error("get_object", code, http_status_code=status)
    with pytest.raises(StoreError) as excinfo:
        store.get_bytes("a/b.json")
    assert not excinfo.value.permanent


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("AccessDenied", 403),
        ("InvalidAccessKeyId", 403),
        ("SignatureDoesNotMatch", 403),
        ("NoSuchBucket", 404),
        ("InvalidArgument", 400),
    ],
)
def test_a_configuration_fault_is_permanent(
    store: S3Store, stub: Stubber, code: str, status: int
) -> None:
    stub.add_client_error("get_object", code, http_status_code=status)
    with pytest.raises(StoreError) as excinfo:
        store.get_bytes("a/b.json")
    assert excinfo.value.permanent


def test_an_unreachable_endpoint_is_a_retryable_store_error(
    store: S3Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unreachable(**_: object) -> None:
        raise EndpointConnectionError(endpoint_url="http://store.invalid:9000")

    monkeypatch.setattr(store._client, "get_object", unreachable)
    with pytest.raises(StoreError) as excinfo:
        store.get_bytes("a/b.json")
    assert not excinfo.value.permanent


def test_an_error_message_names_the_code_and_key_but_not_the_body(
    store: S3Store, stub: Stubber
) -> None:
    stub.add_client_error(
        "get_object",
        "AccessDenied",
        service_message="response body that must never be logged",
        http_status_code=403,
    )
    with pytest.raises(StoreError) as excinfo:
        store.get_bytes("a/b.json")
    message = str(excinfo.value)
    assert "AccessDenied" in message
    assert "a/b.json" in message
    assert "must never be logged" not in message


def test_exists_follows_the_same_rule(store: S3Store, stub: Stubber) -> None:
    _head_ok(stub, "a")
    _head_missing(stub, "b")
    stub.add_client_error("head_object", "403", http_status_code=403)

    assert store.exists("a") is True
    assert store.exists("b") is False
    with pytest.raises(StoreError):
        store.exists("c")


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------


def test_put_bytes_sends_content_type_and_ascii_metadata(
    store: S3Store, stub: Stubber
) -> None:
    stub.add_response(
        "put_object",
        {"ETag": '"abc123"'},
        {
            "Bucket": BUCKET,
            "Key": "a/b.json",
            "Body": b"{}",
            "ContentType": "application/json",
            "Metadata": {"document-id": "d1"},
        },
    )
    stored = store.put_bytes(
        "a/b.json",
        b"{}",
        content_type="application/json",
        metadata={"document-id": "d1"},
    )
    assert stored.key == "a/b.json"
    assert stored.size_bytes == 2
    assert stored.etag == "abc123"


# --------------------------------------------------------------------------
# Listing
# --------------------------------------------------------------------------


def test_a_listing_follows_every_page() -> None:
    store = _store(list_page_size=2)
    with Stubber(store._client) as stub:
        stub.add_response(
            "list_objects_v2",
            {
                "IsTruncated": True,
                "NextContinuationToken": "t1",
                "Contents": [{"Key": "p/1", "Size": 1}, {"Key": "p/2", "Size": 2}],
            },
            {"Bucket": BUCKET, "Prefix": "p/", "MaxKeys": 2},
        )
        stub.add_response(
            "list_objects_v2",
            {"IsTruncated": False, "Contents": [{"Key": "p/3", "Size": 3}]},
            {"Bucket": BUCKET, "Prefix": "p/", "MaxKeys": 2, "ContinuationToken": "t1"},
        )
        keys = [obj.key for obj in store.list_keys("p/")]
        stub.assert_no_pending_responses()
    assert keys == ["p/1", "p/2", "p/3"]


def test_a_pseudo_directory_entry_is_yielded_for_the_caller_to_parse(
    store: S3Store, stub: Stubber
) -> None:
    """Filtering it here would hide the gateway's behaviour from the
    conformance suite; the sink parses keys and ignores it (D12)."""
    stub.add_response(
        "list_objects_v2",
        {"IsTruncated": False, "Contents": [{"Key": "p/dir/", "Size": 0}]},
    )
    assert [obj.key for obj in store.list_keys("p/")] == ["p/dir/"]


def test_a_failed_listing_is_a_store_error(store: S3Store, stub: Stubber) -> None:
    stub.add_client_error("list_objects_v2", "AccessDenied", http_status_code=403)
    with pytest.raises(StoreError, match="ListObjectsV2"):
        list(store.list_keys("p/"))


# --------------------------------------------------------------------------
# Deletes — D3 and D11
# --------------------------------------------------------------------------


def test_a_batch_delete_reports_each_key(store: S3Store, stub: Stubber) -> None:
    """a is deleted, b was already gone, c is refused — three answers, and
    `missing` is only knowable because of the HEAD first."""
    _head_ok(stub, "a")
    _head_missing(stub, "b")
    _head_ok(stub, "c")
    stub.add_response(
        "delete_objects",
        {
            "Deleted": [{"Key": "a"}],
            "Errors": [{"Key": "c", "Code": "AccessDenied", "Message": "no"}],
        },
        {
            "Bucket": BUCKET,
            "Delete": {"Objects": [{"Key": "a"}, {"Key": "c"}], "Quiet": False},
        },
    )

    outcome = store.delete_many(["a", "b", "c"])

    assert outcome == DeleteOutcome(
        deleted=("a",), missing=("b",), failed={"c": "AccessDenied"}
    )


def test_a_delete_of_only_absent_keys_sends_no_delete(
    store: S3Store, stub: Stubber
) -> None:
    _head_missing(stub, "a")
    assert store.delete_many(["a"]) == DeleteOutcome(missing=("a",))


def test_a_key_the_batch_response_omits_is_recorded_as_failed(
    store: S3Store, stub: Stubber
) -> None:
    _head_ok(stub, "a")
    stub.add_response("delete_objects", {"Deleted": []})
    outcome = store.delete_many(["a"])
    assert set(outcome.failed) == {"a"}


def test_a_failed_batch_call_raises(store: S3Store, stub: Stubber) -> None:
    """No key's state is known, so this is not a per-key failure."""
    _head_ok(stub, "a")
    stub.add_client_error("delete_objects", "InternalError", http_status_code=500)
    with pytest.raises(StoreError, match="DeleteObjects"):
        store.delete_many(["a"])


def _many_keys(count: int) -> list[str]:
    return [f"p/{index:05d}.json" for index in range(count)]


def _batch_params(keys: list[str]) -> dict[str, object]:
    return {
        "Bucket": BUCKET,
        "Delete": {"Objects": [{"Key": key} for key in keys], "Quiet": False},
    }


def test_more_than_a_thousand_keys_are_split_into_batches(
    store: S3Store, stub: Stubber
) -> None:
    """DeleteObjects takes at most 1000 keys: 1001 is exactly two calls. The
    Stubber fails the test on a third call or on a different split."""
    keys = _many_keys(1001)
    for key in keys:
        _head_ok(stub, key)
    stub.add_response(
        "delete_objects",
        {"Deleted": [{"Key": key} for key in keys[:1000]]},
        _batch_params(keys[:1000]),
    )
    stub.add_response(
        "delete_objects",
        {"Deleted": [{"Key": keys[1000]}]},
        _batch_params(keys[1000:]),
    )

    outcome = store.delete_many(keys)

    assert sorted(outcome.deleted) == keys
    assert outcome.is_complete


def test_a_failed_later_batch_keeps_the_earlier_batch_and_fails_the_rest(
    store: S3Store, stub: Stubber
) -> None:
    """Raising here would throw away 1000 confirmed deletes; the journal
    needs them and the 1 key that was not confirmed."""
    keys = _many_keys(1001)
    for key in keys:
        _head_ok(stub, key)
    stub.add_response(
        "delete_objects",
        {"Deleted": [{"Key": key} for key in keys[:1000]]},
        _batch_params(keys[:1000]),
    )
    stub.add_client_error("delete_objects", "InternalError", http_status_code=500)

    outcome = store.delete_many(keys)

    assert sorted(outcome.deleted) == keys[:1000]
    assert outcome.failed == {keys[1000]: "InternalError"}


def test_an_unrequested_key_in_the_errors_list_is_ignored(
    store: S3Store, stub: Stubber
) -> None:
    """A gateway reporting on a key it was not asked about must not make the
    outcome claim it, or the base class would refuse the whole outcome."""
    _head_ok(stub, "a")
    stub.add_response(
        "delete_objects",
        {
            "Deleted": [{"Key": "a"}],
            "Errors": [{"Key": "elsewhere", "Code": "AccessDenied"}],
        },
    )
    assert store.delete_many(["a"]) == DeleteOutcome(deleted=("a",))


def test_a_head_failure_part_way_raises_before_any_delete(
    store: S3Store, stub: Stubber
) -> None:
    """Nothing has changed yet, so raising loses nothing; the Stubber fails
    the test if a delete is sent."""
    _head_ok(stub, "a")
    stub.add_client_error("head_object", "AccessDenied", http_status_code=403)
    with pytest.raises(StoreError, match="HeadObject"):
        store.delete_many(["a", "b", "c"])


def test_a_transport_failure_part_way_through_per_key_deletes_fails_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(supports_batch_delete=False)
    sent: list[str] = []

    def delete_object(*, Bucket: str, Key: str) -> dict[str, object]:  # noqa: N803
        if sent:
            raise EndpointConnectionError(endpoint_url="http://store.invalid:9000")
        sent.append(Key)
        return {}

    with Stubber(store._client) as stub:
        for key in ("a", "b", "c"):
            _head_ok(stub, key)
        monkeypatch.setattr(store._client, "delete_object", delete_object)
        outcome = store.delete_many(["a", "b", "c"])

    assert outcome.deleted == ("a",)
    assert outcome.failed == {
        "b": "EndpointConnectionError",
        "c": "EndpointConnectionError",
    }


def test_without_batch_delete_each_key_is_deleted_alone_with_the_same_outcome() -> None:
    """D11: a missing batch API is a performance note, never a blocker. The
    Stubber would fail the test on any DeleteObjects call, since none is
    queued."""
    store = _store(supports_batch_delete=False)
    with Stubber(store._client) as stub:
        _head_ok(stub, "a")
        _head_missing(stub, "b")
        _head_ok(stub, "c")
        stub.add_response("delete_object", {}, {"Bucket": BUCKET, "Key": "a"})
        stub.add_client_error("delete_object", "AccessDenied", http_status_code=403)

        outcome = store.delete_many(["a", "b", "c"])
        stub.assert_no_pending_responses()

    assert not store.capabilities.supports_batch_delete
    assert outcome == DeleteOutcome(
        deleted=("a",), missing=("b",), failed={"c": "AccessDenied"}
    )


# --------------------------------------------------------------------------
# Credentials and the factory (D7)
# --------------------------------------------------------------------------


def test_the_client_uses_its_own_credentials_and_never_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule 6: AWS_* in this container are CKB's own read-only keys. boto3's
    default chain would pick them up; an explicit Session must not."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ckb-read-only-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "ckb-read-only-secret")

    credentials = _store()._client._request_signer._credentials
    assert credentials.access_key == "external-key"
    assert credentials.secret_key == "external-secret"


@pytest.mark.parametrize(
    "blank", ["endpoint_url", "bucket", "access_key_id", "secret_access_key"]
)
def test_a_blank_connection_value_is_refused_rather_than_defaulted(
    monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    """boto3 drops empty credentials and falls back to its default chain,
    which would find CKB's own AWS_* keys here. The store must refuse first,
    whichever caller built it — the manifest store included."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ckb-read-only-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "ckb-read-only-secret")
    values = {
        "endpoint_url": "http://store.invalid:9000",
        "bucket": BUCKET,
        "access_key_id": "external-key",
        "secret_access_key": "external-secret",
    }
    values[blank] = ""

    connection = StoreConnection(
        endpoint_url=values["endpoint_url"],
        bucket=values["bucket"],
        access_key_id=values["access_key_id"],
        secret_access_key=values["secret_access_key"],
        region="us-east-1",
    )

    with pytest.raises(ValueError, match=blank):
        build_store("s3", connection)


def test_addressing_is_path_style() -> None:
    """Every S3-compatible store accepts path style; virtual hosting needs
    wildcard DNS a local or in-cluster store does not have."""
    config = _store()._client.meta.config
    assert config.s3 == {"addressing_style": "path"}


_CONNECTION = StoreConnection(
    endpoint_url="http://store.invalid:9000",
    bucket=BUCKET,
    access_key_id="k",
    secret_access_key="s",
    region="us-east-1",
)


def test_the_factory_builds_the_s3_backend() -> None:
    assert isinstance(build_store("s3", _CONNECTION), S3Store)


def test_a_connection_never_prints_its_credentials() -> None:
    connection = StoreConnection(
        endpoint_url="http://store.invalid:9000",
        bucket=BUCKET,
        access_key_id="AKIDEXAMPLEKEYID",
        secret_access_key="never-print-this-secret",
        region="us-east-1",
    )
    assert "AKIDEXAMPLEKEYID" not in repr(connection)
    assert "never-print-this-secret" not in repr(connection)
    assert BUCKET in repr(connection)


@pytest.mark.parametrize(
    ("backend", "task"), [("azure_blob", "Stage H"), ("local", "D19")]
)
def test_the_factory_names_the_task_that_builds_each_backend(
    backend: str, task: str
) -> None:
    with pytest.raises(ConfigurationError, match=task):
        build_store(backend, _CONNECTION)


def test_the_factory_refuses_an_unknown_backend() -> None:
    with pytest.raises(ConfigurationError, match="not a known backend"):
        build_store("ftp", _CONNECTION)


def test_an_unconfigured_destination_names_every_missing_variable() -> None:
    settings = Settings.model_construct(
        content_external_store_backend="s3",
        external_s3_endpoint_url="",
        external_s3_bucket_name="",
        external_s3_access_key_id=SecretStr(""),
        external_s3_secret_access_key=SecretStr(""),
        external_s3_region="us-east-1",
        external_s3_supports_batch_delete=True,
        export_concurrency=4,
    )
    with pytest.raises(ConfigurationError) as excinfo:
        build_destination_store(settings)
    message = str(excinfo.value)
    for name in (
        "EXTERNAL_S3_ENDPOINT_URL",
        "EXTERNAL_S3_BUCKET_NAME",
        "EXTERNAL_S3_ACCESS_KEY_ID",
        "EXTERNAL_S3_SECRET_ACCESS_KEY",
    ):
        assert name in message


def _destination_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "content_external_store_backend": "s3",
        "external_s3_endpoint_url": "http://store.invalid:9000",
        "external_s3_bucket_name": BUCKET,
        "external_s3_access_key_id": SecretStr("k"),
        "external_s3_secret_access_key": SecretStr("s"),
        "external_s3_region": "us-east-1",
        "external_s3_supports_batch_delete": True,
        "export_concurrency": 4,
        "s3_endpoint_url": "",
        "s3_bucket_name": "",
    }
    values.update(overrides)
    return Settings.model_construct(**values)  # pyright: ignore[reportArgumentType]


def test_the_destination_is_never_ckbs_own_bucket() -> None:
    """Rule 1's bucket-level twin: CKB's bucket holds uploads/scrapped-data/."""
    settings = _destination_settings(
        s3_endpoint_url="http://store.invalid:9000/",
        s3_bucket_name=BUCKET,
    )
    with pytest.raises(ConfigurationError, match="CKB's own bucket"):
        build_destination_store(settings)


def test_a_same_named_bucket_on_another_endpoint_is_allowed() -> None:
    settings = _destination_settings(
        s3_endpoint_url="http://ckb-store.invalid:9000",
        s3_bucket_name=BUCKET,
    )
    assert isinstance(build_destination_store(settings), S3Store)


def test_the_destination_honours_the_batch_delete_declaration() -> None:
    settings = Settings.model_construct(
        content_external_store_backend="s3",
        external_s3_endpoint_url="http://store.invalid:9000",
        external_s3_bucket_name=BUCKET,
        external_s3_access_key_id=SecretStr("k"),
        external_s3_secret_access_key=SecretStr("s"),
        external_s3_region="us-east-1",
        external_s3_supports_batch_delete=False,
        export_concurrency=16,
    )
    store = build_destination_store(settings)
    assert not store.capabilities.supports_batch_delete
    assert store._client.meta.config.max_pool_connections == 32  # pyright: ignore[reportAttributeAccessIssue]
