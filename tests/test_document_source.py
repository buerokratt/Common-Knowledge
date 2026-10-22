import hashlib
import io
import json
import logging
import unicodedata
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from typing import Any
from unittest.mock import Mock

import boto3
import pytest
import requests
from botocore.exceptions import (
    EndpointConnectionError,
    NoCredentialsError,
    ReadTimeoutError,
)
from botocore.response import StreamingBody
from botocore.stub import Stubber

from exporter.api.config import Settings
from exporter.core.diff import SkipReason, classify, needs_read
from exporter.core.ids import chunker_fingerprint
from exporter.core.metadata import MAX_SIDECAR_BYTES, SidecarRejection
from exporter.core.schemas import (
    AgencyWatermark,
    DocumentRef,
    Manifest,
    ManifestDocumentEntry,
    ReadDocument,
)
from exporter.services import document_source
from exporter.services.backoff import BackoffPolicy, is_retryable_http
from exporter.services.document_source import CkbDocumentSource, DocumentSourceError
from exporter.stores.base import StoreError

AGENCY = "agency-1"
TIMESTAMP = "2026-10-20T12:00:00.000+00:00"
ROW = {
    "baseId": "document-1",
    "sourceBaseId": "source-1",
    "url": "https://example.test/page",
    "pageTitle": "Page",
    "subsector": "services",
    "status": "finished",
    "cleanedDataUrl": "s3://ckb/uploads/cleaned.txt",
    "cleanedMetadataUrl": "uploads/cleaned.meta.json",
    "editedDataUrl": None,
    "editedMetadataUrl": None,
    "fileSize": 42,
    "isExcluded": False,
    "isDeleted": False,
    "type": "scraped_file",
    "sourceIsDeleted": False,
    "updatedAt": TIMESTAMP,
}


@pytest.fixture
def resql_settings() -> Settings:
    return Settings(
        content_work_dir="/var/lib/content-external",
        ckb_resql="http://resql.test/ckb",
        ckb_resql_timeout=12.5,
        export_page_size=2,
        s3_endpoint_url="invalid://unused",
        s3_bucket_name="",
    )


# F8's retry with no delay between attempts, so a retried test does not sleep.
FAST_RETRY = BackoffPolicy(max_attempts=3, base_delay=0, max_delay=0)


@pytest.fixture
def source(
    monkeypatch: pytest.MonkeyPatch, resql_settings: Settings
) -> CkbDocumentSource:
    def no_client(*_args: object, **_kwargs: object) -> None:
        pytest.fail("enumeration must not create an S3 client")

    monkeypatch.setattr(boto3, "client", no_client)
    monkeypatch.setattr(boto3.session.Session, "client", no_client)
    return CkbDocumentSource(resql_settings, backoff=FAST_RETRY)


def _stub(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
    *,
    status: int = 200,
    body: bytes | None = None,
) -> dict[str, Any]:
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(payload).encode() if body is None else body
    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: object) -> requests.Response:
        captured["url"] = url
        captured.update(kwargs)
        return response

    monkeypatch.setattr(document_source.requests, "get", fake_get)
    return captured


def _manifest(entry: ManifestDocumentEntry) -> Manifest:
    """A committed manifest that has published document-1 as `entry`."""
    return Manifest(
        schema_version=1,
        manifest_schema_version=1,
        agency_id=AGENCY,
        sink_id="test",
        corpus_watermark=TIMESTAMP,
        document_count=1,
        chunker_fingerprint=entry.chunker_fingerprint,
        committed_at=TIMESTAMP,
        documents={"document-1": entry},
    )


@pytest.mark.parametrize("envelope", [False, True])
def test_enumeration_maps_every_column_without_reading(
    monkeypatch: pytest.MonkeyPatch,
    source: CkbDocumentSource,
    resql_settings: Settings,
    envelope: bool,
) -> None:
    payload = [ROW]
    captured = _stub(monkeypatch, {"response": payload} if envelope else payload)
    assert source.list_documents(AGENCY) == [
        DocumentRef(
            document_id="document-1",
            source_base_id="source-1",
            agency_id=AGENCY,
            url="https://example.test/page",
            page_title="Page",
            subsector="services",
            status="finished",
            is_excluded=False,
            is_deleted=False,
            updated_at=TIMESTAMP,
            content_key="s3://ckb/uploads/cleaned.txt",
            metadata_key="uploads/cleaned.meta.json",
            content_origin="cleaned",
            file_size=42,
        )
    ]
    assert captured == {
        "url": "http://resql.test/ckb/source_file/list_agency_export_documents",
        "params": {
            "agency_base_id": AGENCY,
            "after_base_id": document_source.NIL_UUID,
            "page_size": 2,
        },
        "timeout": resql_settings.ckb_resql_timeout,
    }


def _stub_pages(
    monkeypatch: pytest.MonkeyPatch, pages: list[object]
) -> list[dict[str, str | int]]:
    remaining = iter(pages)
    captured: list[dict[str, str | int]] = []

    def fake_get(
        _url: str, *, params: dict[str, str | int], timeout: float
    ) -> requests.Response:
        captured.append(params)
        page = next(remaining)
        if isinstance(page, requests.RequestException):
            raise page
        if isinstance(page, requests.Response):
            return page
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(page).encode()
        return response

    monkeypatch.setattr(document_source.requests, "get", fake_get)
    return captured


@pytest.mark.parametrize("count", [0, 1, 4, 5])
def test_enumeration_pages_until_a_short_page(
    monkeypatch: pytest.MonkeyPatch,
    source: CkbDocumentSource,
    caplog: pytest.LogCaptureFixture,
    count: int,
) -> None:
    page_size = 2
    rows = [ROW | {"baseId": f"document-{i}"} for i in range(1, count + 1)]
    pages = [rows[i : i + page_size] for i in range(0, count + 1, page_size)]
    captured = _stub_pages(monkeypatch, pages)
    with caplog.at_level(logging.INFO, logger=document_source.__name__):
        documents = source.list_documents(AGENCY)
    assert [ref.document_id for ref in documents] == [row["baseId"] for row in rows]
    assert captured == [
        {
            "agency_base_id": AGENCY,
            "after_base_id": (
                document_source.NIL_UUID if i == 0 else rows[i - 1]["baseId"]
            ),
            "page_size": page_size,
        }
        for i in range(0, count + 1, page_size)
    ]
    assert caplog.messages == [
        f"document enumeration agency={AGENCY} pages={len(pages)} rows={count}"
    ]


@pytest.mark.parametrize(
    "failure",
    [requests.HTTPError("503"), requests.ReadTimeout("timed out"), [{}]],
    ids=["http", "timeout", "invalid-row"],
)
def test_second_page_failure_aborts_enumeration(
    monkeypatch: pytest.MonkeyPatch,
    source: CkbDocumentSource,
    failure: object,
) -> None:
    captured = _stub_pages(
        monkeypatch,
        [
            [ROW, ROW | {"baseId": "document-2"}],
            failure,
            [ROW | {"baseId": "document-5"}],
        ],
    )
    with pytest.raises(DocumentSourceError) as excinfo:
        source.list_documents(AGENCY)
    assert len(captured) == 2
    if isinstance(failure, requests.RequestException):
        assert excinfo.value.__cause__ is failure


@pytest.mark.parametrize(
    "ids",
    [
        [document_source.NIL_UUID],
        ["document-2", "document-2"],
        ["document-2", "document-1"],
        ["document-1", "document-2", "document-2"],
        ["document-1", "document-2", "document-1"],
    ],
)
def test_cursor_must_advance_within_and_between_pages(
    monkeypatch: pytest.MonkeyPatch, source: CkbDocumentSource, ids: list[str]
) -> None:
    rows = [ROW | {"baseId": document_id} for document_id in ids]
    _stub_pages(monkeypatch, [rows[:2], rows[2:]])
    with pytest.raises(DocumentSourceError, match="cursor"):
        source.list_documents(AGENCY)


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({}, (ROW["cleanedDataUrl"], ROW["cleanedMetadataUrl"], "cleaned")),
        (
            {"editedDataUrl": "edited.txt", "editedMetadataUrl": "edited.json"},
            ("edited.txt", "edited.json", "edited"),
        ),
        (
            {"editedDataUrl": "edited.txt"},
            ("edited.txt", ROW["cleanedMetadataUrl"], "edited"),
        ),
        (
            {"editedMetadataUrl": "edited.json"},
            (ROW["cleanedDataUrl"], "edited.json", "cleaned"),
        ),
        (
            {"editedDataUrl": "", "editedMetadataUrl": ""},
            (ROW["cleanedDataUrl"], ROW["cleanedMetadataUrl"], "cleaned"),
        ),
        (
            {"cleanedDataUrl": None, "cleanedMetadataUrl": None},
            (None, None, None),
        ),
    ],
)
def test_content_and_metadata_precedence_are_independent(
    monkeypatch: pytest.MonkeyPatch,
    source: CkbDocumentSource,
    overrides: dict[str, object],
    expected: tuple[str | None, str | None, str | None],
) -> None:
    _stub(monkeypatch, [ROW | overrides])
    ref = source.list_documents(AGENCY)[0]
    assert (ref.content_key, ref.metadata_key, ref.content_origin) == expected


def test_uploaded_file_nullable_fields_pass_through(
    monkeypatch: pytest.MonkeyPatch, source: CkbDocumentSource
) -> None:
    _stub(
        monkeypatch,
        [ROW | {"url": None, "pageTitle": None, "subsector": None, "fileSize": None}],
    )
    ref = source.list_documents(AGENCY)[0]
    assert ref.url is ref.page_title is ref.subsector is ref.file_size is None


@pytest.mark.parametrize("file_type", ["scraped_file", "api_file"])
def test_only_uploaded_files_are_skipped_at_enumeration(
    monkeypatch: pytest.MonkeyPatch, source: CkbDocumentSource, file_type: str
) -> None:
    _stub(monkeypatch, [ROW | {"type": file_type}])
    assert source.list_documents(AGENCY)[0].skip_reason is None


@pytest.mark.parametrize("published", [True, False])
def test_an_uploaded_file_is_skipped_unread_and_held(
    monkeypatch: pytest.MonkeyPatch,
    source: CkbDocumentSource,
    published_entry: ManifestDocumentEntry,
    published: bool,
) -> None:
    """Never read on any run, so it costs no S3 call, and never deleted."""
    _stub(monkeypatch, [ROW | {"type": "uploaded_file", "url": None}])
    refs = source.list_documents(AGENCY)
    fingerprint = published_entry.chunker_fingerprint
    entry = published_entry if published else None
    assert refs[0].skip_reason == document_source.UPLOADED_FILE_UNCITABLE
    assert not needs_read(entry, refs[0], chunker_fingerprint=fingerprint)
    result = classify(
        _manifest(published_entry) if published else None, refs, fingerprint
    )
    assert [change.document_id for change in result.skipped] == ["document-1"]
    assert result.skipped[0].reason == document_source.UPLOADED_FILE_UNCITABLE
    assert result.skipped[0].held is published


@pytest.mark.parametrize("file_type", ["scraped_file", "uploaded_file"])
def test_a_deleted_source_deletes_its_files(
    monkeypatch: pytest.MonkeyPatch,
    source: CkbDocumentSource,
    published_entry: ManifestDocumentEntry,
    file_type: str,
) -> None:
    """CKB marks only the source deleted. The file row still says live and
    finished, so without this signal its chunks would stay published."""
    _stub(monkeypatch, [ROW | {"sourceIsDeleted": True, "type": file_type}])
    refs = source.list_documents(AGENCY)
    assert refs[0].is_deleted is True
    result = classify(
        _manifest(published_entry), refs, published_entry.chunker_fingerprint
    )
    assert [change.document_id for change in result.deleted] == ["document-1"]


def test_empty_enumeration_is_a_list(
    monkeypatch: pytest.MonkeyPatch, source: CkbDocumentSource
) -> None:
    _stub(monkeypatch, [])
    assert source.list_documents(AGENCY) == []


@pytest.mark.parametrize(
    "overrides, bucket, reason",
    [
        ({"isDeleted": True}, "deleted", None),
        ({"isExcluded": True}, "deleted", None),
        ({"status": "not_found"}, "deleted", None),
        ({"status": "scraping"}, "skipped", SkipReason.NOT_READY),
        ({"status": "cleaning"}, "skipped", SkipReason.NOT_READY),
        ({"status": "in_review"}, "skipped", SkipReason.NOT_READY),
        ({"status": "failed"}, "skipped", SkipReason.NOT_READY),
        (
            {"status": "cleaning", "cleanedDataUrl": None},
            "skipped",
            SkipReason.NOT_READY,
        ),
        (
            {"status": "finished", "cleanedDataUrl": None, "editedDataUrl": None},
            "skipped",
            SkipReason.NO_CONTENT,
        ),
    ],
)
def test_all_signals_reach_the_real_diff(
    monkeypatch: pytest.MonkeyPatch,
    source: CkbDocumentSource,
    published_entry: ManifestDocumentEntry,
    overrides: dict[str, object],
    bucket: str,
    reason: SkipReason | None,
) -> None:
    row = ROW | overrides
    _stub(monkeypatch, [row])
    refs = source.list_documents(AGENCY)
    assert len(refs) == 1
    ref = refs[0]
    assert (ref.status, ref.is_deleted, ref.is_excluded, ref.content_key) == (
        row["status"],
        row["isDeleted"],
        row["isExcluded"],
        row["cleanedDataUrl"],
    )
    assert ref.skip_reason is None
    fingerprint = published_entry.chunker_fingerprint
    previous = Manifest(
        schema_version=1,
        manifest_schema_version=1,
        agency_id=AGENCY,
        sink_id="test",
        corpus_watermark=TIMESTAMP,
        document_count=1,
        chunker_fingerprint=fingerprint,
        committed_at=TIMESTAMP,
        documents={"document-1": published_entry},
    )
    result = classify(previous, refs, fingerprint)
    assert [change.document_id for change in getattr(result, bucket)] == ["document-1"]
    assert sum(result.counts().values()) == 1
    if bucket == "skipped":
        assert result.skipped[0].reason == reason
        assert result.skipped[0].held


@pytest.mark.parametrize("column", ROW)
@pytest.mark.parametrize("missing", [True, False])
def test_every_document_column_is_required_and_typed(
    monkeypatch: pytest.MonkeyPatch,
    source: CkbDocumentSource,
    column: str,
    missing: bool,
) -> None:
    row = (
        {k: v for k, v in ROW.items() if k != column} if missing else ROW | {column: []}
    )
    _stub(monkeypatch, [row])
    with pytest.raises(DocumentSourceError, match=column):
        source.list_documents(AGENCY)


@pytest.mark.parametrize(
    "column, value",
    [
        ("baseId", None),
        ("sourceBaseId", None),
        ("status", None),
        ("updatedAt", None),
        ("isExcluded", None),
        ("isDeleted", None),
        ("type", None),
        ("sourceIsDeleted", None),
        ("sourceIsDeleted", 0),
        ("fileSize", True),
        ("fileSize", "42"),
        ("isDeleted", 0),
        ("isExcluded", 1),
    ],
)
def test_required_values_are_not_coerced(
    monkeypatch: pytest.MonkeyPatch,
    source: CkbDocumentSource,
    column: str,
    value: object,
) -> None:
    _stub(monkeypatch, [ROW | {column: value}])
    with pytest.raises(DocumentSourceError, match=column):
        source.list_documents(AGENCY)


@pytest.mark.parametrize("payload", [[None], [[]], [ROW, ROW]])
def test_malformed_or_duplicate_rows_fail_the_enumeration(
    monkeypatch: pytest.MonkeyPatch, source: CkbDocumentSource, payload: object
) -> None:
    _stub(monkeypatch, payload)
    with pytest.raises(DocumentSourceError):
        source.list_documents(AGENCY)


@pytest.mark.parametrize("envelope", [False, True])
@pytest.mark.parametrize("timestamp, count", [(TIMESTAMP, 3), (None, 0)])
def test_watermark(
    monkeypatch: pytest.MonkeyPatch,
    source: CkbDocumentSource,
    resql_settings: Settings,
    envelope: bool,
    timestamp: str | None,
    count: int,
) -> None:
    payload = [{"maxUpdatedAt": timestamp, "liveCount": count}]
    captured = _stub(monkeypatch, {"response": payload} if envelope else payload)
    assert source.watermark(AGENCY) == AgencyWatermark(timestamp, count)
    assert captured == {
        "url": "http://resql.test/ckb/agency/get_agency_export_watermark",
        "params": {"agency_base_id": AGENCY},
        "timeout": resql_settings.ckb_resql_timeout,
    }


@pytest.mark.parametrize(
    "payload",
    [
        [],
        [None],
        [{}, {}],
        [{}],
        [{"maxUpdatedAt": TIMESTAMP}],
        [{"liveCount": 0}],
        [{"maxUpdatedAt": 42, "liveCount": 0}],
        [{"maxUpdatedAt": TIMESTAMP, "liveCount": "3"}],
        [{"maxUpdatedAt": TIMESTAMP, "liveCount": True}],
        [{"maxUpdatedAt": TIMESTAMP, "liveCount": None}],
        [{"maxUpdatedAt": TIMESTAMP, "liveCount": -1}],
    ],
)
def test_watermark_rejects_malformed_rows(
    monkeypatch: pytest.MonkeyPatch, source: CkbDocumentSource, payload: object
) -> None:
    _stub(monkeypatch, payload)
    with pytest.raises(DocumentSourceError):
        source.watermark(AGENCY)


@pytest.mark.parametrize("method", ["watermark", "list_documents"])
@pytest.mark.parametrize(
    "payload", [None, {}, "rows", {"response": {}}, {"response": None}]
)
def test_non_list_body_fails(
    monkeypatch: pytest.MonkeyPatch,
    source: CkbDocumentSource,
    method: str,
    payload: object,
) -> None:
    _stub(monkeypatch, payload)
    with pytest.raises(DocumentSourceError):
        getattr(source, method)(AGENCY)


@pytest.mark.parametrize("method", ["watermark", "list_documents"])
@pytest.mark.parametrize("status, body", [(503, None), (200, b"not json")])
def test_http_and_json_errors_fail(
    monkeypatch: pytest.MonkeyPatch,
    source: CkbDocumentSource,
    method: str,
    status: int,
    body: bytes | None,
) -> None:
    _stub(monkeypatch, [], status=status, body=body)
    with pytest.raises(DocumentSourceError):
        getattr(source, method)(AGENCY)


@pytest.mark.parametrize("method", ["watermark", "list_documents"])
@pytest.mark.parametrize(
    "error, attempts",
    [
        (requests.ConnectionError, FAST_RETRY.max_attempts),
        (requests.ConnectTimeout, FAST_RETRY.max_attempts),
        # The query is still running in Postgres: never stack a second one.
        (requests.ReadTimeout, 1),
    ],
)
def test_unreachable_resql_fails_after_f8s_retries(
    monkeypatch: pytest.MonkeyPatch,
    source: CkbDocumentSource,
    method: str,
    error: type[requests.RequestException],
    attempts: int,
) -> None:
    captured = _stub_pages(monkeypatch, [error("unreachable")] * attempts)
    with pytest.raises(DocumentSourceError):
        getattr(source, method)(AGENCY)
    assert len(captured) == attempts


def test_a_read_timeout_mid_body_is_not_retried(resql_settings: Settings) -> None:
    """requests reports a timeout after the headers as ConnectionError, not
    ReadTimeout (requests/models.py). A real server that sends headers and
    then stalls the body proves the carve-out still holds: one attempt."""
    import socket
    import threading

    listener = socket.create_server(("127.0.0.1", 0))
    connections: list[socket.socket] = []
    stop = threading.Event()

    def serve() -> None:
        listener.settimeout(0.1)
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except TimeoutError:
                continue
            connections.append(conn)
            conn.recv(65536)
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Content-Length: 100\r\n\r\n["
            )

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    port = listener.getsockname()[1]
    settings = resql_settings.model_copy(
        update={"ckb_resql": f"http://127.0.0.1:{port}/ckb", "ckb_resql_timeout": 0.3}
    )
    try:
        with pytest.raises(DocumentSourceError) as excinfo:
            CkbDocumentSource(settings, backoff=FAST_RETRY).watermark(AGENCY)
        cause = excinfo.value.__cause__
        assert isinstance(cause, requests.ConnectionError)
        assert not isinstance(cause, requests.ReadTimeout)
        # F8's general rule would retry it; the carve-out is what stops it.
        assert is_retryable_http(cause)
        assert not document_source.is_retryable_resql(cause)
        assert len(connections) == 1
    finally:
        stop.set()
        thread.join(timeout=5)
        for conn in connections:
            conn.close()
        listener.close()


def _http(status: int) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = b"{}"
    return response


@pytest.mark.parametrize(
    "transient",
    [requests.ConnectionError("blip"), _http(503), _http(429), _http(502)],
    ids=["connection", "503", "429", "502"],
)
def test_a_transient_resql_failure_is_retried(
    monkeypatch: pytest.MonkeyPatch, source: CkbDocumentSource, transient: object
) -> None:
    captured = _stub_pages(monkeypatch, [transient, [ROW]])
    assert [ref.document_id for ref in source.list_documents(AGENCY)] == ["document-1"]
    assert len(captured) == 2


@pytest.mark.parametrize("status", [400, 404, 501])
def test_a_permanent_resql_error_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, source: CkbDocumentSource, status: int
) -> None:
    captured = _stub_pages(monkeypatch, [_http(status)])
    with pytest.raises(DocumentSourceError):
        source.watermark(AGENCY)
    assert len(captured) == 1


def test_the_default_policy_is_f8s(resql_settings: Settings) -> None:
    from exporter.services.backoff import DEFAULT_POLICY

    assert CkbDocumentSource(resql_settings)._backoff is DEFAULT_POLICY


def test_document_source_has_no_destination_setting() -> None:
    assert "CONTENT_SINK" not in Path(document_source.__file__).read_text()


@pytest.fixture
def read_settings() -> Settings:
    return Settings(
        content_work_dir="/var/lib/content-external",
        s3_endpoint_url="http://source.invalid:9000",
        s3_bucket_name="ckb",
        aws_access_key_id="read-key",
        aws_secret_access_key="read-secret",
        aws_region="eu-north-1",
        ckb_s3_connect_timeout=2.5,
        ckb_s3_read_timeout=7.5,
        require_metadata_sidecar=False,
        max_document_bytes=100,
    )


@pytest.fixture
def reader(read_settings: Settings) -> CkbDocumentSource:
    return CkbDocumentSource(read_settings)


@pytest.fixture
def published_entry(read_settings: Settings) -> ManifestDocumentEntry:
    return ManifestDocumentEntry(
        source_base_id="source-1",
        content_origin="cleaned",
        source_updated_at=TIMESTAMP,
        source_status="finished",
        raw_sha256="a" * 64,
        content_sha256="b" * 64,
        metadata_sha256="c" * 64,
        file_size=42,
        chunk_count=1,
        chunker_fingerprint=chunker_fingerprint(read_settings.resolved_chunk_profile),
        state="published",
        processed_at=TIMESTAMP,
    )


@pytest.fixture
def unread_ref() -> DocumentRef:
    return DocumentRef(
        document_id="document-1",
        source_base_id="source-1",
        agency_id=AGENCY,
        url="https://example.test/page",
        page_title="Page",
        subsector=None,
        status="finished",
        is_deleted=False,
        is_excluded=False,
        updated_at=TIMESTAMP,
        content_key="uploads/edited.txt",
        metadata_key=None,
        content_origin="edited",
        file_size=42,
    )


@pytest.fixture
def client_factory(monkeypatch: pytest.MonkeyPatch) -> Iterator[Mock]:
    client = boto3.session.Session(
        aws_access_key_id="stub-key",
        aws_secret_access_key="stub-secret",
        region_name="eu-north-1",
    ).client("s3", endpoint_url="http://source.invalid:9000")
    tracked = Mock(wraps=client)
    factory = Mock(return_value=tracked)
    monkeypatch.setattr(boto3.session.Session, "client", factory)
    yield factory
    # Every read test uses this client: any extra operation fails the contract.
    assert {call[0] for call in tracked.method_calls} <= {"get_object"}


@pytest.fixture
def s3(client_factory: Mock) -> Iterator[Stubber]:
    with Stubber(client_factory.return_value._mock_wraps) as stub:
        yield stub
        stub.assert_no_pending_responses()


class _Body(io.BytesIO):
    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.read_sizes: list[int | None] = []

    def read(self, size: int | None = -1) -> bytes:
        self.read_sizes.append(size)
        return super().read(size)


def _object(
    stub: Stubber, key: str, data: bytes, *, length: int | None = None
) -> _Body:
    body = _Body(data)
    stub.add_response(
        "get_object",
        {
            "Body": StreamingBody(body, len(data)),
            "ContentLength": len(data) if length is None else length,
        },
        {"Bucket": "ckb", "Key": key},
    )
    return body


def _assert_skip(result: ReadDocument, ref: DocumentRef, reason: str) -> None:
    assert result == ReadDocument(
        replace(
            ref,
            raw_sha256=None,
            content_sha256=None,
            metadata_sha256=None,
            skip_reason=reason,
        ),
        text=None,
        sidecar=None,
    )


@pytest.mark.parametrize(
    "updated_at, expected_read",
    [(TIMESTAMP, False), ("2026-10-20T13:00:00.000+00:00", True)],
    ids=["unchanged", "moved"],
)
def test_needs_read_gates_s3_fetches_for_enumerated_timestamps(
    monkeypatch: pytest.MonkeyPatch,
    reader: CkbDocumentSource,
    published_entry: ManifestDocumentEntry,
    s3: Stubber,
    client_factory: Mock,
    updated_at: str,
    expected_read: bool,
) -> None:
    _stub(monkeypatch, [ROW | {"updatedAt": updated_at}])
    if expected_read:
        _object(s3, "uploads/cleaned.meta.json", b"{}")
        _object(s3, "uploads/cleaned.txt", b"text")
    fingerprint = published_entry.chunker_fingerprint
    results = [
        reader.read(ref)
        for ref in reader.list_documents(AGENCY)
        if needs_read(published_entry, ref, chunker_fingerprint=fingerprint)
    ]
    assert [result.text for result in results] == (["text"] if expected_read else [])
    assert client_factory.return_value.get_object.call_count == (
        2 if expected_read else 0
    )


def test_read_normalises_and_hashes_without_changing_source_fields(
    reader: CkbDocumentSource, unread_ref: DocumentRef, s3: Stubber
) -> None:
    ref = replace(unread_ref, metadata_key="s3://ckb/uploads/cleaned.meta.json")
    raw = "\ufeffO\u0303pe\r\n".encode()
    metadata = {"title": "Õpe", "source_url": ref.url}
    sidecar_body = _object(
        s3, "uploads/cleaned.meta.json", json.dumps(metadata).encode()
    )
    content_body = _object(s3, "uploads/edited.txt", raw)
    result = reader.read(ref)
    assert result.text == "Õpe"
    assert unicodedata.is_normalized("NFC", result.text)
    assert result.ref.skip_reason is None
    assert result.sidecar is not None
    metadata_hash = hashlib.sha256(
        json.dumps(metadata, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    assert result.ref == replace(
        ref,
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        content_sha256=hashlib.sha256("Õpe".encode()).hexdigest(),
        metadata_sha256=metadata_hash,
    )
    assert result.sidecar.metadata_sha256 == metadata_hash
    assert content_body.closed and sidecar_body.closed
    assert content_body.read_sizes == [101]
    assert sidecar_body.read_sizes == [MAX_SIDECAR_BYTES + 1]


@pytest.mark.parametrize(
    "content_key", ["uploads/edited.txt", "s3://ckb/uploads/edited.txt"]
)
@pytest.mark.parametrize("metadata_key", ["metadata.json", "s3://ckb/metadata.json"])
def test_both_key_formats(
    reader: CkbDocumentSource,
    unread_ref: DocumentRef,
    s3: Stubber,
    content_key: str,
    metadata_key: str,
) -> None:
    _object(s3, "metadata.json", b"{}")
    _object(s3, "uploads/edited.txt", b"text")
    result = reader.read(
        replace(unread_ref, content_key=content_key, metadata_key=metadata_key)
    )
    assert result.text == "text"
    assert result.ref.skip_reason is None
    assert result.sidecar is not None
    assert result.ref.metadata_sha256 == result.sidecar.metadata_sha256


@pytest.mark.parametrize("field", ["content_key", "metadata_key"])
@pytest.mark.parametrize(
    "key",
    [
        "",
        "s3://another-bucket/file",
        "https://ckb/file",
        "file:///tmp/file",
        "s3://ckb/",
        "s3://ckb",
        "s3:file",
        "//ckb/file",
        "/uploads/file",
        "s3://ckb/file?versionId=other",
        "s3://ckb/file#fragment",
        "s3://[invalid/file",
    ],
)
def test_invalid_keys_skip_without_reading_the_invalid_object(
    reader: CkbDocumentSource,
    unread_ref: DocumentRef,
    s3: Stubber,
    client_factory: Mock,
    field: str,
    key: str,
) -> None:
    """unread_ref has no sidecar, so neither case reads anything: an invalid
    sidecar key is refused before the content is fetched."""
    ref = replace(unread_ref, **{field: key})
    _assert_skip(reader.read(ref), ref, f"{field}_invalid")
    assert client_factory.return_value.get_object.call_count == 0


@pytest.mark.parametrize("declared_oversize", [True, False])
def test_content_size_guard_is_bounded_and_closes_the_body(
    reader: CkbDocumentSource,
    unread_ref: DocumentRef,
    s3: Stubber,
    declared_oversize: bool,
) -> None:
    body = _object(
        s3, "uploads/edited.txt", b"x" * 200, length=200 if declared_oversize else 1
    )
    _assert_skip(reader.read(unread_ref), unread_ref, "content_too_large")
    assert body.closed
    assert body.read_sizes == ([] if declared_oversize else [101])


@pytest.mark.parametrize("raw, text", [(b"", ""), (b"x" * 100, "x" * 100)])
def test_content_at_or_below_limit_succeeds(
    reader: CkbDocumentSource,
    unread_ref: DocumentRef,
    s3: Stubber,
    raw: bytes,
    text: str,
) -> None:
    _object(s3, "uploads/edited.txt", raw)
    result = reader.read(unread_ref)
    assert result.text == text
    assert result.ref.skip_reason is None


def test_non_utf8_content_is_a_skip(
    reader: CkbDocumentSource, unread_ref: DocumentRef, s3: Stubber
) -> None:
    body = _object(s3, "uploads/edited.txt", b"\xff")
    _assert_skip(reader.read(unread_ref), unread_ref, "content_undecodable")
    assert body.closed


@pytest.mark.parametrize("required", [True, False])
@pytest.mark.parametrize("missing_object", [True, False])
def test_absent_sidecar_is_parsed_according_to_policy(
    read_settings: Settings,
    unread_ref: DocumentRef,
    s3: Stubber,
    required: bool,
    missing_object: bool,
) -> None:
    reader = CkbDocumentSource(
        read_settings.model_copy(update={"require_metadata_sidecar": required})
    )
    ref = replace(unread_ref, metadata_key="metadata.json" if missing_object else None)
    if missing_object:
        s3.add_client_error(
            "get_object",
            "NoSuchKey",
            http_status_code=404,
            expected_params={"Bucket": "ckb", "Key": "metadata.json"},
        )
    if not required:
        # A required sidecar that is absent skips before the content is read.
        _object(s3, "uploads/edited.txt", b"text")
    result = reader.read(ref)
    if required:
        _assert_skip(result, ref, SidecarRejection.MISSING.value)
    else:
        assert result.text == "text"
        assert result.ref.skip_reason is None
        assert result.sidecar is not None
        assert result.ref.metadata_sha256 == result.sidecar.metadata_sha256
        assert result.sidecar.sidecar == {}


@pytest.mark.parametrize(
    "metadata, length, reason",
    [
        (b"not json", None, SidecarRejection.INVALID_JSON),
        (b"\xff", None, SidecarRejection.UNDECODABLE),
        (b"[]", None, SidecarRejection.NOT_AN_OBJECT),
        (b"x", MAX_SIDECAR_BYTES + 1, SidecarRejection.TOO_LARGE),
        (b"x" * (MAX_SIDECAR_BYTES + 2), 1, SidecarRejection.TOO_LARGE),
    ],
    # Short ids: the oversize body would otherwise become a test id longer
    # than Windows allows in PYTEST_CURRENT_TEST.
    ids=[
        "invalid-json",
        "undecodable",
        "not-an-object",
        "declared-oversize",
        "oversize",
    ],
)
def test_rejected_sidecar_skips_and_closes_body(
    reader: CkbDocumentSource,
    unread_ref: DocumentRef,
    s3: Stubber,
    client_factory: Mock,
    metadata: bytes,
    length: int | None,
    reason: SidecarRejection,
) -> None:
    """The sidecar is checked first, so its rejection costs one small GET
    and the content is never downloaded."""
    ref = replace(unread_ref, metadata_key="metadata.json")
    body = _object(s3, "metadata.json", metadata, length=length)
    _assert_skip(reader.read(ref), ref, reason.value)
    assert client_factory.return_value.get_object.call_count == 1
    assert body.closed
    assert body.read_sizes == (
        [] if length and length > MAX_SIDECAR_BYTES else [MAX_SIDECAR_BYTES + 1]
    )


def test_missing_content_is_a_skip(
    reader: CkbDocumentSource, unread_ref: DocumentRef, s3: Stubber
) -> None:
    s3.add_client_error("get_object", "NoSuchKey", http_status_code=404)
    _assert_skip(reader.read(unread_ref), unread_ref, "content_missing")


@pytest.mark.parametrize("sidecar_error", [True, False])
@pytest.mark.parametrize(
    "code, status",
    [
        ("AccessDenied", 403),
        ("NoSuchKey", 403),
        ("InternalError", 500),
        ("SlowDown", 503),
        ("NoSuchBucket", 404),
        ("InvalidAccessKeyId", 403),
        # A bare 404 can come from a proxy, not S3, so it is not a missing key.
        ("404", 404),
        ("NotFound", 404),
    ],
)
def test_s3_failures_raise_instead_of_skipping(
    reader: CkbDocumentSource,
    unread_ref: DocumentRef,
    s3: Stubber,
    sidecar_error: bool,
    code: str,
    status: int,
) -> None:
    ref = replace(unread_ref, metadata_key="metadata.json")
    if not sidecar_error:
        _object(s3, "metadata.json", b"{}")
    s3.add_client_error("get_object", code, http_status_code=status)
    with pytest.raises(StoreError):
        reader.read(ref)


@pytest.mark.parametrize(
    "error",
    [
        EndpointConnectionError(endpoint_url="http://source.invalid"),
        ReadTimeoutError(endpoint_url="http://source.invalid"),
        NoCredentialsError(),
    ],
)
def test_connection_timeout_and_credentials_fail_reads(
    reader: CkbDocumentSource,
    unread_ref: DocumentRef,
    client_factory: Mock,
    error: Exception,
) -> None:
    client_factory.return_value.get_object.side_effect = error
    with pytest.raises(StoreError):
        reader.read(unread_ref)


def test_body_read_failure_raises_and_closes(
    reader: CkbDocumentSource, unread_ref: DocumentRef, s3: Stubber
) -> None:
    body = Mock()
    body.read.side_effect = ReadTimeoutError(endpoint_url="http://source.invalid")
    s3.add_response("get_object", {"Body": body, "ContentLength": 10})
    with pytest.raises(StoreError):
        reader.read(unread_ref)
    body.close.assert_called_once()


def test_client_is_lazy_reused_and_given_explicit_credentials(
    monkeypatch: pytest.MonkeyPatch,
    read_settings: Settings,
    reader: CkbDocumentSource,
    unread_ref: DocumentRef,
    s3: Stubber,
    client_factory: Mock,
) -> None:
    session_factory = Mock(wraps=boto3.session.Session)
    monkeypatch.setattr(boto3.session, "Session", session_factory)
    _stub(monkeypatch, [])
    assert reader.list_documents(AGENCY) == []
    _stub(monkeypatch, [{"maxUpdatedAt": None, "liveCount": 0}])
    assert reader.watermark(AGENCY) == AgencyWatermark(None, 0)
    assert session_factory.call_count == client_factory.call_count == 0
    for _ in range(2):
        _object(s3, "uploads/edited.txt", b"text")
        assert reader.read(unread_ref).text == "text"
    session_factory.assert_called_once_with(
        aws_access_key_id="read-key",
        aws_secret_access_key="read-secret",
        region_name="eu-north-1",
    )
    assert client_factory.call_count == 1
    assert client_factory.call_args.args == ("s3",)
    kwargs = client_factory.call_args.kwargs
    assert kwargs["endpoint_url"] == "http://source.invalid:9000"
    # F8 for CKB's bucket: botocore's standard mode, as for the destination.
    assert kwargs["config"].retries == {"mode": "standard", "total_max_attempts": 3}
    assert kwargs["config"].connect_timeout == read_settings.ckb_s3_connect_timeout
    assert kwargs["config"].read_timeout == read_settings.ckb_s3_read_timeout


class _Raw(io.BytesIO):
    """A raw HTTP body as botocore reads it: streamed and read."""

    def stream(self, **_kwargs: object) -> Iterator[bytes]:
        yield self.getvalue()


_SLOW_DOWN = (503, b"<Error><Code>SlowDown</Code></Error>")
_INTERNAL = (500, b"<Error><Code>InternalError</Code></Error>")
_DENIED = (403, b"<Error><Code>AccessDenied</Code></Error>")
_TEXT = (200, b"text")


@pytest.mark.parametrize(
    "answers, attempts, outcome",
    [
        ([_SLOW_DOWN, _TEXT], 2, "read"),
        ([_INTERNAL, _TEXT], 2, "read"),
        ([_DENIED], 1, "raised"),
        # Three in all, counting the first: total_max_attempts, not botocore's
        # max_attempts, which would be read as retries and make it four.
        ([_SLOW_DOWN] * 4, 3, "raised"),
    ],
    ids=["throttled", "server-error", "denied", "throttled-every-time"],
)
def test_ckb_reads_retry_transient_s3_errors_through_botocore(
    read_settings: Settings,
    unread_ref: DocumentRef,
    answers: list[tuple[int, bytes]],
    attempts: int,
    outcome: str,
) -> None:
    """F8 for CKB's bucket, through botocore's real retry handler. Stubber
    returns before that handler runs, so it cannot show a retry; a
    before-send response is processed exactly like one off the wire."""
    from botocore.awsrequest import AWSResponse

    responses = [
        AWSResponse(
            "http://source.invalid",
            status,
            {"Content-Length": str(len(body))} if status == 200 else {},
            _Raw(body),
        )
        for status, body in answers
    ]
    sent: list[object] = []

    def respond(request: object, **_kwargs: object) -> AWSResponse:
        sent.append(request)
        return responses[len(sent) - 1]

    source = CkbDocumentSource(read_settings)
    bucket = source._bucket
    bucket._ensure_client()
    bucket._client.meta.events.register("before-send.s3.GetObject", respond)

    if outcome == "read":
        assert source.read(unread_ref).text == "text"
    else:
        with pytest.raises(StoreError):
            source.read(unread_ref)
    assert len(sent) == attempts


def test_concurrent_first_reads_share_one_client(
    reader: CkbDocumentSource, unread_ref: DocumentRef, client_factory: Mock
) -> None:
    barrier = Barrier(4)

    def response(**_kwargs: object) -> dict[str, object]:
        return {"Body": StreamingBody(io.BytesIO(b"text"), 4), "ContentLength": 4}

    client_factory.return_value.get_object.side_effect = response

    def read(_index: int) -> ReadDocument:
        barrier.wait(timeout=5)
        return reader.read(unread_ref)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(read, range(4)))
    assert [result.text for result in results] == ["text"] * 4
    assert client_factory.call_count == 1


@pytest.mark.parametrize(
    "missing",
    [
        ["s3_endpoint_url"],
        ["s3_bucket_name"],
        ["aws_access_key_id"],
        ["aws_secret_access_key"],
        [
            "s3_endpoint_url",
            "s3_bucket_name",
            "aws_access_key_id",
            "aws_secret_access_key",
        ],
    ],
)
def test_missing_read_settings_name_every_variable(
    read_settings: Settings,
    unread_ref: DocumentRef,
    client_factory: Mock,
    missing: list[str],
) -> None:
    settings = read_settings.model_dump() | dict.fromkeys(missing, "")
    reader = CkbDocumentSource(Settings(**settings))
    with pytest.raises(StoreError) as excinfo:
        reader.read(unread_ref)
    for name in missing:
        assert name.upper() in str(excinfo.value)
    assert client_factory.call_count == 0


@pytest.mark.parametrize(
    "error", [NoCredentialsError(), ValueError("invalid endpoint")]
)
def test_client_creation_errors_are_store_errors(
    reader: CkbDocumentSource,
    unread_ref: DocumentRef,
    client_factory: Mock,
    error: Exception,
) -> None:
    client_factory.side_effect = error
    with pytest.raises(StoreError):
        reader.read(unread_ref)
