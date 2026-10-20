import hashlib
import io
import json
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
    "updatedAt": TIMESTAMP,
}


@pytest.fixture
def source(monkeypatch: pytest.MonkeyPatch) -> CkbDocumentSource:
    def no_client(*_args: object, **_kwargs: object) -> None:
        pytest.fail("enumeration must not create an S3 client")

    monkeypatch.setattr(boto3, "client", no_client)
    monkeypatch.setattr(boto3.session.Session, "client", no_client)
    return CkbDocumentSource(
        Settings(
            content_work_dir="/var/lib/content-external",
            ckb_resql="http://resql.test/ckb",
            s3_endpoint_url="invalid://unused",
            s3_bucket_name="",
        )
    )


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


@pytest.mark.parametrize("envelope", [False, True])
def test_enumeration_maps_every_column_without_reading(
    monkeypatch: pytest.MonkeyPatch, source: CkbDocumentSource, envelope: bool
) -> None:
    payload = [ROW]
    captured = _stub(monkeypatch, {"response": payload} if envelope else payload)
    documents = source.list_documents(AGENCY)
    assert isinstance(documents, list)
    assert len(documents) == 1
    ref = documents[0]
    assert (
        ref.document_id,
        ref.source_base_id,
        ref.agency_id,
        ref.url,
        ref.page_title,
        ref.subsector,
        ref.status,
        ref.is_excluded,
        ref.is_deleted,
        ref.updated_at,
        ref.file_size,
    ) == (
        ROW["baseId"],
        ROW["sourceBaseId"],
        AGENCY,
        ROW["url"],
        ROW["pageTitle"],
        ROW["subsector"],
        ROW["status"],
        ROW["isExcluded"],
        ROW["isDeleted"],
        TIMESTAMP,
        42,
    )
    assert ref.raw_sha256 is ref.content_sha256 is ref.metadata_sha256 is None
    assert ref.skip_reason is None
    assert captured == {
        "url": "http://resql.test/ckb/source_file/list_agency_export_documents",
        "params": {"agency_base_id": AGENCY},
        "timeout": 30.0,
    }


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
        "timeout": 30.0,
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
@pytest.mark.parametrize("error", [requests.Timeout, requests.ConnectionError])
def test_unreachable_resql_fails(
    monkeypatch: pytest.MonkeyPatch,
    source: CkbDocumentSource,
    method: str,
    error: type[requests.RequestException],
) -> None:
    def fake_get(_url: str, **_kwargs: object) -> None:
        raise error("unreachable")

    monkeypatch.setattr(document_source.requests, "get", fake_get)
    with pytest.raises(DocumentSourceError):
        getattr(source, method)(AGENCY)


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
        _object(s3, "uploads/cleaned.txt", b"text")
        _object(s3, "uploads/cleaned.meta.json", b"{}")
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
    content_body = _object(s3, "uploads/edited.txt", raw)
    sidecar_body = _object(
        s3, "uploads/cleaned.meta.json", json.dumps(metadata).encode()
    )
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
    _object(s3, "uploads/edited.txt", b"text")
    _object(s3, "metadata.json", b"{}")
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
    ref = replace(unread_ref, **{field: key})
    if field == "metadata_key":
        _object(s3, "uploads/edited.txt", b"text")
    _assert_skip(reader.read(ref), ref, f"{field}_invalid")
    assert client_factory.return_value.get_object.call_count == (
        1 if field == "metadata_key" else 0
    )


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
    _object(s3, "uploads/edited.txt", b"text")
    if missing_object:
        s3.add_client_error(
            "get_object",
            "NoSuchKey",
            http_status_code=404,
            expected_params={"Bucket": "ckb", "Key": "metadata.json"},
        )
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
)
def test_rejected_sidecar_skips_and_closes_body(
    reader: CkbDocumentSource,
    unread_ref: DocumentRef,
    s3: Stubber,
    metadata: bytes,
    length: int | None,
    reason: SidecarRejection,
) -> None:
    ref = replace(unread_ref, metadata_key="metadata.json")
    _object(s3, "uploads/edited.txt", b"text")
    body = _object(s3, "metadata.json", metadata, length=length)
    _assert_skip(reader.read(ref), ref, reason.value)
    assert body.closed
    assert body.read_sizes == (
        [] if length and length > MAX_SIDECAR_BYTES else [MAX_SIDECAR_BYTES + 1]
    )


def test_missing_content_is_a_skip(
    reader: CkbDocumentSource, unread_ref: DocumentRef, s3: Stubber
) -> None:
    s3.add_client_error("get_object", "NoSuchKey", http_status_code=404)
    _assert_skip(reader.read(unread_ref), unread_ref, "content_missing")


@pytest.mark.parametrize("code", ["404", "NotFound"])
def test_bare_404_raises_instead_of_skipping(
    reader: CkbDocumentSource, unread_ref: DocumentRef, s3: Stubber, code: str
) -> None:
    s3.add_client_error("get_object", code, http_status_code=404)
    with pytest.raises(StoreError):
        reader.read(unread_ref)


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
    if sidecar_error:
        _object(s3, "uploads/edited.txt", b"text")
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
    assert kwargs["config"].retries == {"total_max_attempts": 1}


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
