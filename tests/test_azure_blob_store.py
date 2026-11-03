"""Azure's SDK responses mapped to the store contract, without a network.

The shared conformance assertions run unchanged over a stateful SDK fake.
A transport fake also exercises the real SDK retry policy, so a constructor
option alone cannot pretend that the attempt bound is enforced.
"""

import json
from collections.abc import Callable, Iterator
from unittest.mock import Mock

import pytest
import test_store_conformance as conformance
from azure.core.exceptions import (
    AzureError,
    ClientAuthenticationError,
    HttpResponseError,
    ServiceRequestError,
    ServiceResponseError,
)
from azure.core.paging import ItemPaged
from azure.core.pipeline.transport import HttpRequest, HttpResponse, RequestsTransport
from azure.storage.blob import (
    BlobClient,
    BlobProperties,
    ContainerClient,
    ContentSettings,
    ExponentialRetry,
    StorageErrorCode,
)

from exporter.stores import azure_blob_store
from exporter.stores.azure_blob_store import AzureBlobStore
from exporter.stores.base import ExternalStoreClient, StoreError

ACCOUNT_URL = "https://store.invalid"
CONTAINER = "agency-content"
KEY = "a/metadata.json"


class _Response(HttpResponse):
    def __init__(
        self, status: int, code: str | None = None, request: HttpRequest | None = None
    ) -> None:
        super().__init__(request or HttpRequest("HEAD", ACCOUNT_URL), None)
        self.status_code = status
        self.reason = "test response"
        self.headers = {"Content-Length": "0", "ETag": '"abc123"'}
        if code:
            self.headers["x-ms-error-code"] = code

    def body(self) -> bytes:
        return b""


def _error(code: str | None, status: int) -> HttpResponseError:
    return HttpResponseError(
        "private response body https://store.invalid/?sig=secret",
        response=_Response(status, code),
    )


def _store(
    *, supports_batch_delete: bool = True, list_page_size: int = 1000
) -> AzureBlobStore:
    return AzureBlobStore(
        account_url=ACCOUNT_URL,
        container=CONTAINER,
        credential="sv=test&sig=secret",
        supports_batch_delete=supports_batch_delete,
        list_page_size=list_page_size,
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Mock:
    fake = Mock(spec=ContainerClient)
    fake.get_blob_client.return_value = Mock(spec=BlobClient)
    fake.get_blob_client.return_value.upload_blob.return_value = {"etag": '"abc123"'}
    monkeypatch.setattr(azure_blob_store, "ContainerClient", Mock(return_value=fake))
    return fake


@pytest.fixture
def store(client: Mock) -> AzureBlobStore:
    return _store()


def test_get_bytes_returns_the_body(store: AzureBlobStore, client: Mock) -> None:
    client.download_blob.return_value.readall.return_value = "õpe".encode()
    assert store.get_bytes(KEY) == "õpe".encode()


@pytest.mark.parametrize("operation", ["get_bytes", "exists"])
@pytest.mark.parametrize(
    "code,status,permanent",
    [
        ("BlobNotFound", 404, None),
        ("ContainerNotFound", 404, True),
        (None, 404, True),
        ("AuthenticationFailed", 403, True),
        ("InvalidHeaderValue", 400, True),
        ("RequestTimeout", 408, False),
        ("LeaseIdMissing", 409, False),
        ("TooManyRequests", 429, False),
        ("InternalError", 500, False),
        ("ServerBusy", 503, False),
    ],
)
def test_only_blob_not_found_is_absence(
    store: AzureBlobStore,
    client: Mock,
    operation: str,
    code: str | None,
    status: int,
    permanent: bool | None,
) -> None:
    error = _error(code, status)
    client.download_blob.side_effect = error
    client.get_blob_client.return_value.get_blob_properties.side_effect = error
    read = getattr(store, operation)
    if permanent is None:
        assert read(KEY) is (None if operation == "get_bytes" else False)
    else:
        with pytest.raises(StoreError) as excinfo:
            read(KEY)
        assert excinfo.value.permanent is permanent
        assert excinfo.value.__cause__ is error
        message = str(excinfo.value)
        assert KEY in message
        assert (code or "Unknown") in message
        assert "private response body" not in message
        assert "https://" not in message
        assert "secret" not in message


def test_exists_returns_true_for_a_blob(store: AzureBlobStore) -> None:
    assert store.exists(KEY) is True


@pytest.mark.parametrize("operation", ["get_bytes", "exists"])
def test_the_sdk_error_code_enum_also_identifies_absence(
    store: AzureBlobStore, client: Mock, operation: str
) -> None:
    error = _error(None, 404)
    error.error_code = StorageErrorCode.BLOB_NOT_FOUND
    client.download_blob.side_effect = error
    client.get_blob_client.return_value.get_blob_properties.side_effect = error
    assert getattr(store, operation)(KEY) is (
        None if operation == "get_bytes" else False
    )


@pytest.mark.parametrize("operation", ["get_bytes", "exists"])
@pytest.mark.parametrize(
    "error,permanent",
    [
        (ServiceRequestError("connection failed"), False),
        (ServiceResponseError("read timed out"), False),
        (ClientAuthenticationError("bad credential"), True),
    ],
)
def test_sdk_failures_preserve_their_cause_and_retry_classification(
    store: AzureBlobStore,
    client: Mock,
    operation: str,
    error: AzureError,
    permanent: bool,
) -> None:
    client.download_blob.side_effect = error
    client.get_blob_client.return_value.get_blob_properties.side_effect = error
    with pytest.raises(StoreError) as excinfo:
        getattr(store, operation)(KEY)
    assert excinfo.value.__cause__ is error
    assert excinfo.value.permanent is permanent


def test_a_failure_while_reading_the_body_is_not_absence(
    store: AzureBlobStore, client: Mock
) -> None:
    client.download_blob.return_value.readall.side_effect = ServiceResponseError(
        "timeout"
    )
    with pytest.raises(StoreError, match="download_blob") as excinfo:
        store.get_bytes(KEY)
    assert not excinfo.value.permanent


def test_put_bytes_sends_content_type_and_ascii_metadata(
    store: AzureBlobStore, client: Mock
) -> None:
    result = store.put_bytes(
        KEY, b"{}", content_type="application/json", metadata={"document_id": "d1"}
    )
    args, kwargs = client.get_blob_client.return_value.upload_blob.call_args
    assert args == (b"{}",)
    assert kwargs["overwrite"] is True
    assert kwargs["content_settings"].content_type == "application/json"
    assert kwargs["metadata"] == {"document_id": "d1"}
    assert (result.key, result.size_bytes, result.etag) == (KEY, 2, "abc123")


def test_an_upload_error_is_a_store_error(store: AzureBlobStore, client: Mock) -> None:
    error = _error("AuthenticationFailed", 403)
    client.get_blob_client.return_value.upload_blob.side_effect = error
    with pytest.raises(StoreError, match="upload_blob") as excinfo:
        store.put_bytes(KEY, b"{}")
    assert excinfo.value.permanent
    assert excinfo.value.__cause__ is error


@pytest.mark.parametrize(
    "metadata,key",
    [
        ({"title": "õäöü"}, "title"),
        ({"document-id": "d1"}, "document-id"),
        ({"1id": "d1"}, "1id"),
        ({"": "private value"}, "''"),
    ],
)
def test_invalid_metadata_is_refused_before_upload(
    store: AzureBlobStore, client: Mock, metadata: dict[str, str], key: str
) -> None:
    with pytest.raises(ValueError) as excinfo:
        store.put_bytes(KEY, b"{}", metadata=metadata)
    assert key in str(excinfo.value)
    assert next(iter(metadata.values())) not in str(excinfo.value)
    client.get_blob_client.return_value.upload_blob.assert_not_called()


def test_put_json_preserves_estonian_in_the_body(
    store: AzureBlobStore, client: Mock
) -> None:
    document = {"title": "õäöü"}
    store.put_json(KEY, document)
    args, kwargs = client.get_blob_client.return_value.upload_blob.call_args
    assert args[0] == '{"title": "õäöü"}'.encode()
    assert json.loads(args[0]) == document
    assert kwargs["content_settings"].content_type == "application/json; charset=utf-8"


def test_a_listing_follows_every_page(client: Mock) -> None:
    pages_read: list[str | None] = []

    def next_page(token: str | None) -> list[BlobProperties]:
        pages_read.append(token)
        start = int(token or "0")
        return [
            BlobProperties(name=f"p/{i}", **{"Content-Length": i, "ETag": '"etag"'})
            for i in range(start, min(start + 2, 5))
        ]

    def extract(page: list[BlobProperties]) -> tuple[str | None, list[BlobProperties]]:
        next_index = int(page[-1].name.split("/")[-1]) + 1
        return str(next_index) if next_index < 5 else None, page

    client.list_blobs.return_value = ItemPaged(next_page, extract)
    objects = list(_store(list_page_size=2).list_keys("p/"))
    assert [(obj.key, obj.size_bytes, obj.etag) for obj in objects] == [
        (f"p/{i}", i, "etag") for i in range(5)
    ]
    assert pages_read == [None, "2", "4"]
    client.list_blobs.assert_called_once_with(name_starts_with="p/", results_per_page=2)


def test_an_error_mid_listing_preserves_the_cause(
    store: AzureBlobStore, client: Mock
) -> None:
    error = _error("ServerBusy", 503)

    def pages() -> Iterator[BlobProperties]:
        yield BlobProperties(name="p/first", **{"Content-Length": 1})
        raise error

    client.list_blobs.return_value = pages()
    listing = store.list_keys("p/")
    assert next(listing).key == "p/first"
    with pytest.raises(StoreError, match="list_blobs.*p/.*ServerBusy") as excinfo:
        next(listing)
    assert excinfo.value.__cause__ is error
    assert not excinfo.value.permanent


@pytest.mark.parametrize("batch", [True, False])
def test_deletes_report_deleted_missing_and_failed_per_key(
    client: Mock, batch: bool
) -> None:
    responses = [
        _Response(202),
        _Response(404, "BlobNotFound"),
        _Response(404, "ContainerNotFound"),
        _Response(404),
        _Response(403, "AuthorizationFailure"),
        _Response(409, "SnapshotsPresent"),
    ]
    client.delete_blobs.return_value = iter(responses)
    client.delete_blob.side_effect = [
        None if response.status_code == 202 else HttpResponseError(response=response)
        for response in responses
    ]
    result = _store(supports_batch_delete=batch).delete_many(
        ["a", "b", "c", "d", "e", "f"]
    )
    assert result.deleted == ("a",)
    assert result.missing == ("b",)
    assert result.failed == {
        "c": "ContainerNotFound",
        "d": "Unknown",
        "e": "AuthorizationFailure",
        "f": "SnapshotsPresent",
    }
    client.get_blob_client.assert_not_called()
    if batch:
        client.delete_blobs.assert_called_once_with(
            "a", "b", "c", "d", "e", "f", raise_on_any_failure=False
        )
    else:
        assert [call.args[0] for call in client.delete_blob.call_args_list] == list(
            "abcdef"
        )


def test_more_than_256_deletes_are_split_into_batches(
    store: AzureBlobStore, client: Mock
) -> None:
    keys = [f"p/{i}" for i in range(513)]
    client.delete_blobs.side_effect = [
        iter([_Response(202)] * size) for size in (256, 256, 1)
    ]
    assert store.delete_many(keys).deleted == tuple(keys)
    assert [len(call.args) for call in client.delete_blobs.call_args_list] == [
        256,
        256,
        1,
    ]


def test_a_failed_later_batch_keeps_the_earlier_batch_and_fails_the_rest(
    store: AzureBlobStore, client: Mock
) -> None:
    keys = [f"p/{i}" for i in range(513)]
    client.delete_blobs.side_effect = [
        iter([_Response(202)] * 256),
        _error("ServerBusy", 503),
    ]
    result = store.delete_many(keys)
    assert result.deleted == tuple(keys[:256])
    assert result.failed == dict.fromkeys(keys[256:], "ServerBusy")
    assert client.delete_blobs.call_count == 2


@pytest.mark.parametrize("batch", [True, False])
def test_a_transport_failure_after_one_result_preserves_it(
    client: Mock, batch: bool
) -> None:
    error = ServiceResponseError("private timeout details")

    def responses() -> Iterator[HttpResponse]:
        yield _Response(202)
        raise error

    client.delete_blobs.return_value = responses()
    client.delete_blob.side_effect = [None, error]
    result = _store(supports_batch_delete=batch).delete_many(["a", "b", "c"])
    assert result.deleted == ("a",)
    assert result.failed == {"b": "ServiceResponseError", "c": "ServiceResponseError"}


@pytest.mark.parametrize("batch", [True, False])
def test_a_transport_failure_before_any_delete_raises(
    client: Mock, batch: bool
) -> None:
    error = ServiceRequestError("private connection details")
    client.delete_blobs.side_effect = error
    client.delete_blob.side_effect = error
    with pytest.raises(StoreError) as excinfo:
        _store(supports_batch_delete=batch).delete_many(["a", "b"])
    assert not excinfo.value.permanent
    assert excinfo.value.__cause__ is error


def test_an_unreported_delete_is_failed(store: AzureBlobStore, client: Mock) -> None:
    client.delete_blobs.return_value = iter([_Response(202)])
    result = store.delete_many(["a", "b"])
    assert result.deleted == ("a",)
    assert result.failed == {"b": "not-reported-by-backend"}


@pytest.mark.parametrize(
    "code,status,permanent",
    [("AuthenticationFailed", 403, True), ("ServerBusy", 503, False)],
)
def test_a_failed_first_batch_raises_without_losing_a_result(
    store: AzureBlobStore, client: Mock, code: str, status: int, permanent: bool
) -> None:
    error = _error(code, status)
    client.delete_blobs.side_effect = error
    with pytest.raises(StoreError, match=code) as excinfo:
        store.delete_many(["a", "b"])
    assert "'a (+1 more)'" in str(excinfo.value)
    assert excinfo.value.permanent is permanent
    assert excinfo.value.__cause__ is error


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"account_url": ""}, "account_url"),
        ({"account_url": " "}, "account_url"),
        ({"container": ""}, "container"),
        ({"container": " "}, "container"),
        ({"credential": None}, "credential"),
        ({"credential": ""}, "credential"),
        ({"credential": " "}, "credential"),
        ({"credential": {}}, "credential"),
        ({"account_url": ACCOUNT_URL + "?sig=secret"}, "account_url"),
        ({"list_page_size": 0}, "list_page_size"),
    ],
)
def test_invalid_constructor_values_are_refused(
    client: Mock, overrides: dict[str, object], match: str
) -> None:
    arguments: dict[str, object] = {
        "account_url": ACCOUNT_URL,
        "container": CONTAINER,
        "credential": "key",
    }
    arguments.update(overrides)
    with pytest.raises(ValueError, match=match) as excinfo:
        AzureBlobStore(**arguments)
    assert "secret" not in str(excinfo.value)
    azure_blob_store.ContainerClient.assert_not_called()


@pytest.mark.parametrize(
    "credential",
    [
        "key",
        "sv=test&sig=secret",
        {"account_name": "account", "account_key": "key"},
        object(),
    ],
)
def test_credentials_are_passed_unchanged(client: Mock, credential: object) -> None:
    AzureBlobStore(account_url=ACCOUNT_URL, container=CONTAINER, credential=credential)
    assert azure_blob_store.ContainerClient.call_args.kwargs["credential"] is credential


@pytest.mark.parametrize("statuses", [[503, 200], [503, 503, 503, 200]])
@pytest.mark.parametrize(
    "error_type", [None, ServiceRequestError, ServiceResponseError]
)
def test_sdk_retries_are_bounded_to_three_attempts(
    monkeypatch: pytest.MonkeyPatch,
    statuses: list[int],
    error_type: type[AzureError] | None,
) -> None:
    sent: list[HttpRequest] = []

    def respond(
        _transport: RequestsTransport, request: HttpRequest, **kwargs: object
    ) -> HttpResponse:
        sent.append(request)
        assert _transport.connection_config.timeout == 5
        assert _transport.connection_config.read_timeout == 60
        status = statuses[len(sent) - 1]
        if error_type is not None and status != 200:
            raise error_type("transport failure")
        return _Response(status, request=request)

    waits: list[float] = []

    # Records the backoff the policy computed instead of sleeping it.
    def record(
        policy: ExponentialRetry, settings: dict[str, object], _transport: object
    ) -> None:
        waits.append(policy.get_backoff_time(settings))

    monkeypatch.setattr(RequestsTransport, "send", respond)
    monkeypatch.setattr(ExponentialRetry, "sleep", record)
    store = _store()
    if len(statuses) == 2:
        assert store.exists(KEY)
        assert len(sent) == 2
    else:
        with pytest.raises(StoreError):
            store.exists(KEY)
        assert len(sent) == 3
    assert len(waits) == len(sent) - 1
    # The policy counts a retry before sleeping, so retry n waits around
    # initial + base**n, give or take the jitter.
    ceilings = [
        azure_blob_store._INITIAL_BACKOFF_SECONDS
        + azure_blob_store._BACKOFF_INCREMENT_BASE**retry
        + azure_blob_store._BACKOFF_JITTER_SECONDS
        for retry in range(1, len(waits) + 1)
    ]
    assert all(
        0 <= wait <= ceiling for wait, ceiling in zip(waits, ceilings, strict=True)
    )
    assert sum(waits) <= sum(ceilings)


@pytest.fixture
def memory_client(client: Mock) -> Mock:
    objects: dict[str, bytes] = {}

    def read(key: str) -> bytes:
        if key not in objects:
            raise _error("BlobNotFound", 404)
        return objects[key]

    def blob_client(key: str) -> Mock:
        blob = Mock(spec=BlobClient)

        def upload(
            payload: bytes,
            *,
            overwrite: bool,
            content_settings: ContentSettings,
            metadata: dict[str, str],
        ) -> dict[str, str]:
            assert overwrite
            objects[key] = payload
            return {"etag": '"etag"'}

        blob.upload_blob.side_effect = upload
        blob.get_blob_properties.side_effect = lambda: BlobProperties(
            **{"Content-Length": len(read(key))}
        )
        return blob

    def listing(
        *, name_starts_with: str, results_per_page: int
    ) -> Iterator[BlobProperties]:
        for key, payload in objects.items():
            if key.startswith(name_starts_with):
                yield BlobProperties(
                    name=key, **{"Content-Length": len(payload), "ETag": '"etag"'}
                )

    def delete(key: str) -> None:
        read(key)
        del objects[key]

    def delete_batch(*keys: str, raise_on_any_failure: bool) -> Iterator[HttpResponse]:
        assert not raise_on_any_failure
        for key in keys:
            try:
                delete(key)
            except HttpResponseError:
                yield _Response(404, "BlobNotFound")
            else:
                yield _Response(202)

    client.get_blob_client.side_effect = blob_client
    client.download_blob.side_effect = lambda key: Mock(readall=lambda: read(key))
    client.list_blobs.side_effect = listing
    client.delete_blob.side_effect = delete
    client.delete_blobs.side_effect = delete_batch
    return client


@pytest.mark.parametrize("batch", [True, False])
@pytest.mark.parametrize(
    "check",
    [
        conformance.test_a_nested_key_round_trips_byte_identically,
        conformance.test_an_overwrite_replaces_the_object,
        conformance.test_a_missing_key_is_absent_not_an_error,
        conformance.test_a_delete_reports_deleted_and_missing,
        conformance.test_a_replayed_delete_is_all_missing,
        conformance.test_a_listing_paginates_past_the_page_size,
        conformance.test_a_listing_stays_inside_its_prefix,
        conformance.test_a_conditional_write_is_not_supported,
    ],
    ids=lambda check: check.__name__,
)
def test_shared_store_conformance(
    memory_client: Mock, batch: bool, check: Callable[[ExternalStoreClient, str], None]
) -> None:
    store = _store(supports_batch_delete=batch, list_page_size=2)
    conformance.test_a_store_declares_every_capability_the_pipeline_needs(store)
    assert store.capabilities.supports_batch_delete is batch
    check(store, "conformance/")
    memory_client.create_container.assert_not_called()
