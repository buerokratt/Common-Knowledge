import json
from pathlib import Path
from typing import Any

import boto3
import pytest
import requests

from exporter.api.config import Settings
from exporter.core.diff import classify
from exporter.core.ids import chunker_fingerprint
from exporter.core.schemas import AgencyWatermark, Manifest, ManifestDocumentEntry
from exporter.services import document_source
from exporter.services.document_source import CkbDocumentSource, DocumentSourceError

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
    "overrides, bucket",
    [
        ({"isDeleted": True}, "deleted"),
        ({"isExcluded": True}, "deleted"),
        ({"status": "not_found"}, "deleted"),
        ({"status": "scraping"}, "skipped"),
        ({"status": "cleaning"}, "skipped"),
        ({"status": "in_review"}, "skipped"),
        ({"status": "failed"}, "skipped"),
        ({"status": "scraped", "cleanedDataUrl": None}, "skipped"),
    ],
)
def test_all_signals_reach_the_real_diff(
    monkeypatch: pytest.MonkeyPatch,
    source: CkbDocumentSource,
    overrides: dict[str, object],
    bucket: str,
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
    fingerprint = chunker_fingerprint(
        Settings(content_work_dir="/var/lib/content-external").resolved_chunk_profile
    )
    entry = ManifestDocumentEntry(
        source_base_id="source-1",
        content_origin="cleaned",
        source_updated_at=TIMESTAMP,
        source_status="finished",
        raw_sha256="a" * 64,
        content_sha256="b" * 64,
        metadata_sha256="c" * 64,
        file_size=42,
        chunk_count=1,
        chunker_fingerprint=fingerprint,
        state="published",
        processed_at=TIMESTAMP,
    )
    previous = Manifest(
        schema_version=1,
        manifest_schema_version=1,
        agency_id=AGENCY,
        sink_id="test",
        corpus_watermark=TIMESTAMP,
        document_count=1,
        chunker_fingerprint=fingerprint,
        committed_at=TIMESTAMP,
        documents={"document-1": entry},
    )
    result = classify(previous, refs, fingerprint)
    assert [change.document_id for change in getattr(result, bucket)] == ["document-1"]
    assert sum(result.counts().values()) == 1


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


def test_read_remains_unimplemented(
    monkeypatch: pytest.MonkeyPatch, source: CkbDocumentSource
) -> None:
    _stub(monkeypatch, [ROW])
    ref = source.list_documents(AGENCY)[0]
    with pytest.raises(NotImplementedError):
        source.read(ref)


def test_document_source_has_no_destination_setting() -> None:
    assert "CONTENT_SINK" not in Path(document_source.__file__).read_text()
