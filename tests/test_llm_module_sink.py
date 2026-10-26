"""The llm-module sink, L1-L8: contract, payload, keying, batching, partial
failure, idempotency and metadata-only merge.

Every test drives the real sink through a real requests.Session into the
in-process fake in tests/llm_module_fake.py, so headers, timeouts,
raise_for_status and the backoff wrapper all run as in production. No
network, no far-side dependency.

The D15 conformance suite does not run against this sink yet: its harness
observes object-store keys. Adding an HTTP harness is L15/L16.
"""

import hashlib
import json
import logging
import math
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
import requests
from exporter_fakes import FakeDocumentSource, InMemoryStore, make_row
from llm_module_fake import FakeLlmModule, Reply, Seen, session_for
from pydantic import ValidationError

from exporter.core.chunking import Chunker
from exporter.core.constants import CHUNK_PROFILES
from exporter.core.ids import chunker_fingerprint, make_chunk_id
from exporter.core.schemas import Chunk, DocumentRecord, TextSpan, to_json_dict
from exporter.services.manifest_service import ManifestService
from exporter.services.manifest_store import ManifestStore
from exporter.services.publish_service import PublishCoordinator
from exporter.sinks.base import NotSupported, UpsertStatus
from exporter.sinks.llm_module_contract import (
    AGREED_TERMS,
    ContractTerms,
    IngestPart,
    IngestRequest,
    IngestResponse,
    classify_item_status,
)
from exporter.sinks.llm_module_sink import ITEM_RETRY_ROUNDS, LlmModuleSink

AGENCY = "agency-1"
BASE_URL = "https://llm-module.invalid"
TOKEN = "tok-3f9a-not-a-real-credential"
SINK_ID = "llm_module"
CONTRACT_DOC = (
    Path(__file__).resolve().parents[1]
    / "content-external"
    / "docs"
    / "llm-module-ingest-contract.md"
)
MERGE_TERMS = ContractTerms(supports_metadata_only_merge=True, max_concurrency=2)


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------


def _record(
    document_id: str = "d1",
    chunk_count: int = 3,
    *,
    title: str = "Õppetoetus",
) -> DocumentRecord:
    return DocumentRecord(
        document_id=document_id,
        source_base_id="s1",
        content_origin="cleaned",
        source_url="https://www.eesti.ee/teenus",
        source={"page_title": title, "source_url": "https://www.eesti.ee/teenus"},
        raw_sha256="r" * 64,
        content_sha256="c" * 64,
        metadata_sha256="m" * 64,
        chunk_count=chunk_count,
        synced_at="2026-10-26T10:17:00Z",
    )


def _text(ordinal: int) -> str:
    return f"3. detsember, lõik {ordinal}"


def _chunks(
    document_id: str = "d1",
    count: int = 3,
    *,
    text: Callable[[int], str] = _text,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    start = 0
    for ordinal in range(count):
        body = text(ordinal)
        chunks.append(
            Chunk(
                chunk_id=make_chunk_id(AGENCY, document_id, ordinal),
                document_id=document_id,
                ordinal=ordinal,
                text=body,
                span=TextSpan(start, start + len(body)),
            )
        )
        start += len(body)
    return chunks


def _sink(
    fake: FakeLlmModule,
    *,
    terms: ContractTerms = AGREED_TERMS,
    max_request_bytes: int = 8_388_608,
    token_provider: Callable[[], str] = lambda: TOKEN,
    run_id: str = "run-1",
    sleeps: list[float] | None = None,
) -> LlmModuleSink:
    recorded = sleeps if sleeps is not None else []
    return LlmModuleSink(
        base_url=BASE_URL,
        agency_id=AGENCY,
        sink_id=SINK_ID,
        run_id=run_id,
        token_provider=token_provider,
        ingest_schema_version=1,
        max_request_bytes=max_request_bytes,
        connect_timeout=5.0,
        read_timeout=120.0,
        terms=terms,
        session=session_for(fake),
        sleep=recorded.append,
        rand=lambda: 0.5,
    )


@pytest.fixture
def fake() -> FakeLlmModule:
    return FakeLlmModule()


def _id(ordinal: int, document_id: str = "d1") -> str:
    return make_chunk_id(AGENCY, document_id, ordinal)


# --------------------------------------------------------------------------
# L1 — the sink and what it declares
# --------------------------------------------------------------------------


def test_capabilities_are_declared_from_the_code_and_the_agreed_terms(
    fake: FakeLlmModule,
) -> None:
    capabilities = _sink(fake).capabilities

    assert capabilities.can_list is False
    assert capabilities.can_read_back is False
    assert capabilities.supports_batch_delete is False
    assert capabilities.atomic_per_document is False
    assert capabilities.supports_agency_scoped_delete is False
    assert capabilities.supports_metadata_only_merge is False
    assert capabilities.max_concurrency == AGREED_TERMS.max_concurrency


def test_the_metadata_merge_capability_follows_the_terms(fake: FakeLlmModule) -> None:
    assert _sink(fake, terms=MERGE_TERMS).capabilities.supports_metadata_only_merge


@pytest.mark.parametrize(
    ("verb", "match"),
    [
        (lambda s: s.delete_documents(["d1"]), "task L9"),
        (lambda s: s.delete_chunks("d1", [3]), "task L9"),
        (
            lambda s: s.sweep_stale(AGENCY, datetime.now(UTC) - timedelta(hours=1)),
            "task L10",
        ),
        (lambda s: s.delete_agency(AGENCY), "supports_agency_scoped_delete"),
        (lambda s: s.read_document("d1"), "can_read_back"),
        (lambda s: s.holds_agency_content(), "can_list"),
    ],
)
def test_what_is_not_built_yet_refuses_rather_than_no_ops(
    fake: FakeLlmModule, verb: Callable[[LlmModuleSink], object], match: str
) -> None:
    with pytest.raises(NotSupported, match=match):
        verb(_sink(fake))
    assert fake.requests == []


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"base_url": "http://llm-module.invalid"}, "https://"),
        ({"ingest_schema_version": 2}, "ingest_schema_version"),
        ({"run_id": "run/1"}, "run_id"),
        ({"max_request_bytes": 0}, "max_request_bytes"),
        ({"read_timeout": 0}, "timeout"),
    ],
)
def test_construction_refuses_bad_settings(
    overrides: dict[str, Any], match: str
) -> None:
    arguments: dict[str, Any] = {
        "base_url": BASE_URL,
        "agency_id": AGENCY,
        "sink_id": SINK_ID,
        "run_id": "run-1",
        "token_provider": lambda: TOKEN,
        "ingest_schema_version": 1,
        "max_request_bytes": 1024,
        "connect_timeout": 5.0,
        "read_timeout": 120.0,
    }
    arguments.update(overrides)
    with pytest.raises(ValueError, match=match):
        LlmModuleSink(**arguments)


def test_the_default_session_verifies_tls() -> None:
    sink = LlmModuleSink(
        base_url=BASE_URL,
        agency_id=AGENCY,
        sink_id=SINK_ID,
        run_id="run-1",
        token_provider=lambda: TOKEN,
        ingest_schema_version=1,
        max_request_bytes=1024,
        connect_timeout=5.0,
        read_timeout=120.0,
    )
    assert sink._session.verify is True


# --------------------------------------------------------------------------
# L2 — the contract, as a document and as code
# --------------------------------------------------------------------------


def _contract_examples() -> list[tuple[str, dict[str, Any]]]:
    text = CONTRACT_DOC.read_text(encoding="utf-8")
    found = re.findall(
        r"<!-- example: (request|response) -->\s*```json\n(.*?)```", text, re.S
    )
    return [(kind, json.loads(body)) for kind, body in found]


def test_every_json_example_in_the_contract_document_validates() -> None:
    examples = _contract_examples()
    kinds = [kind for kind, _ in examples]
    assert kinds.count("request") >= 2
    assert kinds.count("response") >= 2
    for kind, example in examples:
        model = IngestRequest if kind == "request" else IngestResponse
        model.model_validate(example)


def test_the_documented_request_is_what_the_sink_sends(fake: FakeLlmModule) -> None:
    """The example's field names are exactly the sink's, so the document
    cannot describe a request the code never builds."""
    documented = next(body for kind, body in _contract_examples() if kind == "request")
    _sink(fake).upsert_document(_record(), _chunks())
    assert set(fake.requests[0].body) == set(documented)
    assert set(fake.requests[0].body["chunks"][0]) == set(documented["chunks"][0])


def test_the_request_model_refuses_a_field_it_does_not_declare() -> None:
    example = next(body for kind, body in _contract_examples() if kind == "request")
    with pytest.raises(ValidationError):
        IngestRequest.model_validate({**example, "embedding_model": "x"})


def test_the_response_model_tolerates_fields_it_does_not_know() -> None:
    IngestResponse.model_validate(
        {"ingest_schema_version": 1, "items": [], "took_ms": 12}
    )


@pytest.mark.parametrize(
    ("index", "count", "final"), [(0, 2, True), (1, 2, False), (2, 2, True)]
)
def test_a_part_must_mark_only_its_last_as_final(
    index: int, count: int, final: bool
) -> None:
    with pytest.raises(ValidationError):
        IngestPart(index=index, count=count, final=final)


@pytest.mark.parametrize(
    ("status", "verdict"),
    [
        (200, "applied"),
        (201, "applied"),
        (409, "retryable"),
        (422, "retryable"),
        (503, "retryable"),
        (400, "permanent"),
        (404, "permanent"),
        (500, "permanent"),
    ],
)
def test_item_statuses_classify_per_key_design_6(status: int, verdict: str) -> None:
    assert classify_item_status(status) == verdict


# --------------------------------------------------------------------------
# L3 — the payload
# --------------------------------------------------------------------------


def test_the_request_carries_the_record_exactly_as_the_core_built_it(
    fake: FakeLlmModule,
) -> None:
    record = _record()
    chunks = _chunks()

    _sink(fake).upsert_document(record, chunks)

    body = fake.requests[0].body
    expected = {
        **cast("dict[str, Any]", to_json_dict(record)),
        "ingest_schema_version": 1,
        "agency_id": AGENCY,
        "part": {"index": 0, "count": 1, "final": True},
        "chunks": [
            {
                "chunk_id": chunk.chunk_id,
                "ordinal": chunk.ordinal,
                "text": chunk.text,
                "char_start": chunk.span.start,
                "char_end": chunk.span.end,
            }
            for chunk in chunks
        ],
    }
    assert body == expected


def test_estonian_text_is_sent_as_utf8_and_never_escaped(fake: FakeLlmModule) -> None:
    _sink(fake).upsert_document(_record(title="Õppetoetus šokk ž"), _chunks())

    raw = fake.requests[0].raw
    assert "Õppetoetus šokk ž".encode() in raw
    assert "lõik".encode() in raw
    assert b"\\u00d5" not in raw


def test_the_request_is_authenticated_typed_and_bounded_in_time(
    fake: FakeLlmModule,
) -> None:
    _sink(fake).upsert_document(_record(), _chunks())

    seen = fake.requests[0]
    assert seen.headers["Authorization"] == f"Bearer {TOKEN}"
    assert seen.headers["Content-Type"] == "application/json; charset=utf-8"
    assert seen.timeout == (5.0, 120.0)
    assert seen.verify is True


# --------------------------------------------------------------------------
# L4 — chunk_id is the point id
# --------------------------------------------------------------------------


def test_chunk_ids_go_out_untouched(fake: FakeLlmModule) -> None:
    _sink(fake).upsert_document(_record(), _chunks())

    assert fake.requests[0].chunk_ids == [_id(0), _id(1), _id(2)]
    assert set(fake.points) == {_id(0), _id(1), _id(2)}


def test_editing_one_chunk_overwrites_one_point_in_place(fake: FakeLlmModule) -> None:
    sink = _sink(fake)
    sink.upsert_document(_record(), _chunks())
    before = dict(fake.points)

    edited = _chunks(text=lambda o: "parandatud lõik" if o == 1 else _text(o))
    outcome = sink.upsert_document(_record(), edited)

    assert outcome.published
    assert set(fake.points) == set(before)
    assert fake.points[_id(1)]["text"] == "parandatud lõik"
    assert fake.points[_id(0)]["text"] == before[_id(0)]["text"]


# --------------------------------------------------------------------------
# L5 — one document per request, split under the cap
# --------------------------------------------------------------------------


def test_a_document_that_fits_is_one_request(fake: FakeLlmModule) -> None:
    outcome = _sink(fake).upsert_document(_record(), _chunks())

    assert outcome.status is UpsertStatus.PUBLISHED
    assert outcome.chunks_written == 3
    assert len(fake.requests) == 1


def test_a_document_over_the_cap_goes_as_ordered_parts_each_with_its_record(
    fake: FakeLlmModule,
) -> None:
    count = 40
    cap = 1500
    record = _record(chunk_count=count)

    outcome = _sink(fake, max_request_bytes=cap).upsert_document(
        record, _chunks(count=count)
    )

    assert outcome.published
    assert outcome.chunks_written == count
    parts = [seen.body["part"] for seen in fake.requests]
    assert len(parts) > 1
    assert [p["index"] for p in parts] == list(range(len(parts)))
    assert {p["count"] for p in parts} == {len(parts)}
    assert [p["final"] for p in parts] == [False] * (len(parts) - 1) + [True]
    for seen in fake.requests:
        assert len(seen.raw) <= cap
        assert seen.body["document_id"] == "d1"
        assert seen.body["source"] == to_json_dict(record.source)
        assert seen.body["chunk_count"] == count
    sent = [chunk["ordinal"] for seen in fake.requests for chunk in seen.body["chunks"]]
    assert sent == list(range(count))


def test_the_largest_document_splits_under_the_default_cap(
    fake: FakeLlmModule,
) -> None:
    """MAX_CHUNKS_PER_DOCUMENT (5000) at the widest profile's 1200
    characters, all two-byte letters: ~12 MB, so the split path is reached
    at the default 8 MiB cap rather than assumed unreachable."""
    count = 5000
    record = _record(chunk_count=count)

    outcome = _sink(fake).upsert_document(
        record, _chunks(count=count, text=lambda _: "õ" * 1200)
    )

    assert outcome.published
    assert len(fake.requests) >= 2
    assert all(len(seen.raw) <= 8_388_608 for seen in fake.requests)
    assert len(fake.points) == count


def test_a_rejected_record_stops_the_remaining_parts(fake: FakeLlmModule) -> None:
    fake.fail_item("record", 400, "invalid-record")

    outcome = _sink(fake, max_request_bytes=1500).upsert_document(
        _record(chunk_count=40), _chunks(count=40)
    )

    assert outcome.status is UpsertStatus.FAILED_PERMANENT
    assert outcome.chunks_written == 0
    assert outcome.detail == "record-400:invalid-record"
    assert len(fake.requests) == 1
    assert fake.points == {}


def test_a_document_that_cannot_fit_is_refused_without_sending(
    fake: FakeLlmModule,
) -> None:
    outcome = _sink(fake, max_request_bytes=300).upsert_document(_record(), _chunks())

    assert outcome.status is UpsertStatus.FAILED_PERMANENT
    assert outcome.detail == "request-cap-too-small"
    assert fake.requests == []


def test_a_document_with_no_chunks_is_one_request_with_an_empty_list(
    fake: FakeLlmModule,
) -> None:
    outcome = _sink(fake).upsert_document(_record(chunk_count=0), [])

    assert outcome.published
    assert fake.requests[0].body["chunks"] == []
    assert "d1" in fake.records


def _oversized(chunk_count: int) -> DocumentRecord:
    """A record whose sidecar alone is over a 2 KB cap."""
    return replace(_record(chunk_count=chunk_count), source={"page_title": "õ" * 2000})


@pytest.mark.parametrize(
    ("terms", "chunks"),
    [(AGREED_TERMS, []), (MERGE_TERMS, None)],
    ids=["no-chunks", "metadata-only"],
)
def test_a_record_that_cannot_fit_alone_is_refused_without_sending(
    fake: FakeLlmModule, terms: ContractTerms, chunks: list[Chunk] | None
) -> None:
    """With no chunks to trip the per-chunk check, the record alone must be
    measured — not left to fail as a RuntimeError at send time."""
    outcome = _sink(fake, terms=terms, max_request_bytes=2048).upsert_document(
        _oversized(0 if chunks is not None else 3), chunks
    )

    assert outcome.status is UpsertStatus.FAILED_PERMANENT
    assert outcome.detail == "request-cap-too-small"
    assert fake.requests == []


def test_text_that_cannot_be_encoded_fails_permanently_without_sending(
    fake: FakeLlmModule, caplog: pytest.LogCaptureFixture
) -> None:
    """A lone surrogate is a str but not UTF-8. The UnicodeEncodeError quotes
    the text around it, so it must become a code, not escape the sink."""
    caplog.set_level(logging.DEBUG)
    chunks = _chunks(text=lambda o: "lõik \ud800" if o == 1 else _text(o))

    outcome = _sink(fake).upsert_document(_record(), chunks)

    assert outcome.status is UpsertStatus.FAILED_PERMANENT
    assert outcome.detail == "unencodable-document"
    assert fake.requests == []
    assert "lõik" not in caplog.text


def test_a_non_finite_number_in_the_sidecar_goes_as_null(fake: FakeLlmModule) -> None:
    """JSON has no NaN. pydantic's JSON mode writes it as null, so a sidecar
    that Python's json parser let through still sends, as valid JSON."""
    record = replace(_record(), source={"score": math.nan, "page_title": "Õ"})

    outcome = _sink(fake).upsert_document(record, _chunks())

    assert outcome.published
    assert b"NaN" not in fake.requests[0].raw
    assert fake.requests[0].body["source"] == {"score": None, "page_title": "Õ"}


# --------------------------------------------------------------------------
# L6 — per-item status and partial failure
# --------------------------------------------------------------------------


def test_retryable_items_are_resent_alone_and_the_document_publishes(
    fake: FakeLlmModule,
) -> None:
    sleeps: list[float] = []
    fake.fail_item(_id(1), 503, "embedder-busy")

    outcome = _sink(fake, sleeps=sleeps).upsert_document(_record(), _chunks())

    assert outcome.status is UpsertStatus.PUBLISHED
    assert outcome.chunks_written == 3
    assert [seen.chunk_ids for seen in fake.requests] == [
        [_id(0), _id(1), _id(2)],
        [_id(1)],
    ]
    assert len(sleeps) == 1
    assert set(fake.points) == {_id(0), _id(1), _id(2)}


def test_a_permanent_item_fails_the_document(fake: FakeLlmModule) -> None:
    fake.fail_item(_id(2), 400, "too-long")

    outcome = _sink(fake).upsert_document(_record(), _chunks())

    assert outcome.status is UpsertStatus.FAILED_PERMANENT
    assert outcome.chunks_written == 2
    assert outcome.detail == "chunk-400:too-long"
    assert len(fake.requests) == 1


def test_items_still_failing_after_the_rounds_are_retryable(
    fake: FakeLlmModule,
) -> None:
    fake.fail_item(_id(0), 503, times=ITEM_RETRY_ROUNDS + 1)

    outcome = _sink(fake).upsert_document(_record(), _chunks())

    assert outcome.status is UpsertStatus.FAILED_RETRYABLE
    assert outcome.chunks_written == 2
    assert outcome.detail == "items-still-failing: 503"
    assert len(fake.requests) == ITEM_RETRY_ROUNDS + 1


def test_a_retryable_record_resends_the_record_with_every_chunk(
    fake: FakeLlmModule,
) -> None:
    fake.fail_item("record", 503)

    outcome = _sink(fake).upsert_document(_record(), _chunks())

    assert outcome.published
    assert [seen.chunk_ids for seen in fake.requests] == [
        [_id(0), _id(1), _id(2)],
        [_id(0), _id(1), _id(2)],
    ]


def test_an_unacknowledged_item_is_resent(fake: FakeLlmModule) -> None:
    fake.drop_item(_id(0))

    outcome = _sink(fake).upsert_document(_record(), _chunks())

    assert outcome.published
    assert fake.requests[1].chunk_ids == [_id(0)]


def test_a_2xx_envelope_with_failed_items_is_not_success(fake: FakeLlmModule) -> None:
    """The fake answers 207 here; the sink must read the items, not the code."""
    fake.fail_item(_id(0), 404)

    outcome = _sink(fake).upsert_document(_record(), _chunks())

    assert not outcome.published


def test_a_200_with_a_failed_item_is_read_like_a_207(fake: FakeLlmModule) -> None:
    """A far side that answers 200 when it should say 207 still fails the
    document: the items decide, never the envelope."""
    items = [
        {"ref": "record", "status": 200},
        {"ref": _id(0), "status": 200},
        {"ref": _id(1), "status": 404, "code": "gone"},
        {"ref": _id(2), "status": 200},
    ]
    fake.reply_next(
        Reply(
            200, body=json.dumps({"ingest_schema_version": 1, "items": items}).encode()
        )
    )

    outcome = _sink(fake).upsert_document(_record(), _chunks())

    assert outcome.status is UpsertStatus.FAILED_PERMANENT
    assert outcome.detail == "chunk-404:gone"
    assert outcome.chunks_written == 2


def _part_ordinals(seen: Seen) -> list[int]:
    return [chunk["ordinal"] for chunk in seen.body["chunks"]]


def test_a_retryable_item_in_the_first_part_of_a_split_is_resent_alone(
    fake: FakeLlmModule,
) -> None:
    fake.fail_item(_id(0), 503)

    outcome = _sink(fake, max_request_bytes=1500).upsert_document(
        _record(chunk_count=40), _chunks(count=40)
    )

    assert outcome.published
    assert outcome.chunks_written == 40
    first_round = [seen for seen in fake.requests if ":r" not in seen.key]
    assert len(first_round) > 1
    assert [_part_ordinals(s) for s in fake.requests[len(first_round) :]] == [[0]]
    assert len(fake.points) == 40


def test_a_retryable_record_in_a_later_part_resends_that_part_onwards(
    fake: FakeLlmModule,
) -> None:
    """Part 0 lands; part 1's record does not, so part 1 applied nothing and
    no later part is sent. The next round resends everything from part 1 on —
    and nothing from part 0."""
    fake.fail_item("record", 503, after=1)

    outcome = _sink(fake, max_request_bytes=1500).upsert_document(
        _record(chunk_count=40), _chunks(count=40)
    )

    assert outcome.published
    assert outcome.chunks_written == 40
    keys = [seen.key for seen in fake.requests]
    assert keys[:2] == ["run-1:d1:p0", "run-1:d1:p1"]
    assert all(":r1" in key for key in keys[2:])
    landed_first = _part_ordinals(fake.requests[0])
    resent = [o for seen in fake.requests[2:] for o in _part_ordinals(seen)]
    assert resent == list(range(len(landed_first), 40))
    assert len(fake.points) == 40


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b'{"items": []}',
        json.dumps(
            {
                "ingest_schema_version": 1,
                "items": [
                    {"ref": "record", "status": 200},
                    {"ref": "record", "status": 200},
                ],
            }
        ).encode(),
        json.dumps(
            {
                "ingest_schema_version": 1,
                "items": [{"ref": "c-never-sent", "status": 200}],
            }
        ).encode(),
    ],
    ids=["not-json", "no-version", "duplicate-ref", "unknown-ref"],
)
def test_an_untrustworthy_response_is_retryable(
    fake: FakeLlmModule, body: bytes
) -> None:
    fake.reply_next(Reply(200, body=body))

    outcome = _sink(fake).upsert_document(_record(), _chunks())

    assert outcome.status is UpsertStatus.FAILED_RETRYABLE
    assert outcome.detail == "malformed-response"


def test_a_schema_version_mismatch_is_permanent() -> None:
    fake = FakeLlmModule(schema_version=2)

    outcome = _sink(fake).upsert_document(_record(), _chunks())

    assert outcome.status is UpsertStatus.FAILED_PERMANENT
    assert outcome.detail == "schema-version-mismatch"


def test_a_429_is_retried_after_the_time_it_asked_for(fake: FakeLlmModule) -> None:
    sleeps: list[float] = []
    fake.reply_next(Reply(429, headers={"Retry-After": "7"}))

    outcome = _sink(fake, sleeps=sleeps).upsert_document(_record(), _chunks())

    assert outcome.published
    assert sleeps == [7.0]
    assert len(fake.requests) == 2


def test_a_5xx_is_retried(fake: FakeLlmModule) -> None:
    fake.reply_next(Reply(503), Reply(502))

    outcome = _sink(fake).upsert_document(_record(), _chunks())

    assert outcome.published
    assert len(fake.requests) == 3


@pytest.mark.parametrize(
    ("reply", "detail"),
    [
        (Reply(401), "http-401"),
        (Reply(403), "http-403"),
        (Reply(413), "http-413"),
        (Reply(204), "http-204"),
        (Reply(302, headers={"Location": "https://elsewhere.invalid/"}), "http-302"),
        (requests.exceptions.SSLError("certificate verify failed"), "tls-error"),
    ],
)
def test_a_refused_request_is_permanent_and_not_retried(
    fake: FakeLlmModule, reply: Reply | BaseException, detail: str
) -> None:
    fake.reply_next(reply)

    outcome = _sink(fake).upsert_document(_record(), _chunks())

    assert outcome.status is UpsertStatus.FAILED_PERMANENT
    assert outcome.detail == detail
    assert len(fake.requests) == 1


@pytest.mark.parametrize(
    ("error", "detail"),
    [
        (requests.ConnectionError("refused"), "connection-error"),
        (requests.ReadTimeout("slow"), "timeout"),
        (requests.exceptions.ChunkedEncodingError("reset"), "truncated-response"),
        (requests.exceptions.ContentDecodingError("bad gzip"), "truncated-response"),
    ],
)
def test_a_transport_failure_that_persists_is_retryable(
    fake: FakeLlmModule, error: BaseException, detail: str
) -> None:
    fake.reply_next(*[error] * 4)

    outcome = _sink(fake).upsert_document(_record(), _chunks())

    assert outcome.status is UpsertStatus.FAILED_RETRYABLE
    assert outcome.detail == detail
    assert len(fake.requests) == 4


def test_a_response_cut_off_once_is_retried(fake: FakeLlmModule) -> None:
    fake.reply_next(requests.exceptions.ChunkedEncodingError("reset"))

    outcome = _sink(fake).upsert_document(_record(), _chunks())

    assert outcome.published
    assert len(fake.requests) == 2


def test_a_response_past_its_bound_is_not_read_whole(fake: FakeLlmModule) -> None:
    fake.reply_next(Reply(200, body=b" " * ((1 << 20) + 1)))

    outcome = _sink(fake, max_request_bytes=1500).upsert_document(_record(), _chunks())

    assert outcome.status is UpsertStatus.FAILED_PERMANENT
    assert outcome.detail == "response-too-large"
    assert len(fake.requests) == 1


def _token_file_missing() -> str:
    raise OSError("vault agent token file missing")


@pytest.mark.parametrize(
    "provider",
    [
        lambda: "",
        lambda: "   ",
        lambda: "a\nb",
        lambda: "a\tb",
        lambda: "tõken",
        _token_file_missing,
    ],
    ids=["empty", "blank", "newline", "control", "non-ascii", "raises"],
)
def test_no_usable_credential_fails_permanently_without_sending(
    fake: FakeLlmModule, provider: Callable[[], str]
) -> None:
    outcome = _sink(fake, token_provider=provider).upsert_document(_record(), _chunks())

    assert outcome.status is UpsertStatus.FAILED_PERMANENT
    assert outcome.detail == "credential-unavailable"
    assert fake.requests == []


def test_the_credential_is_fetched_for_every_upsert(fake: FakeLlmModule) -> None:
    tokens = iter(["first", "second"])
    sink = _sink(fake, token_provider=lambda: next(tokens))

    sink.upsert_document(_record("d1"), _chunks("d1"))
    sink.upsert_document(_record("d2"), _chunks("d2"))

    assert [seen.headers["Authorization"] for seen in fake.requests] == [
        "Bearer first",
        "Bearer second",
    ]


# --------------------------------------------------------------------------
# L7 — idempotency
# --------------------------------------------------------------------------


def test_idempotency_keys_name_the_run_document_part_and_round(
    fake: FakeLlmModule,
) -> None:
    fake.fail_item(_id(0), 503)

    _sink(fake, max_request_bytes=1500).upsert_document(
        _record(chunk_count=40), _chunks(count=40)
    )

    keys = [seen.key for seen in fake.requests]
    first_round = [k for k in keys if ":r" not in k]
    assert first_round == [f"run-1:d1:p{i}" for i in range(len(first_round))]
    assert keys[len(first_round) :] == ["run-1:d1:p0:r1"]


def test_every_key_ends_with_the_digest_of_its_body(fake: FakeLlmModule) -> None:
    fake.fail_item(_id(0), 503)

    _sink(fake, max_request_bytes=1500).upsert_document(
        _record(chunk_count=40), _chunks(count=40)
    )

    for seen in fake.requests:
        assert seen.key_digest == hashlib.sha256(seen.raw).hexdigest()[:16]
    assert len({seen.headers["Idempotency-Key"] for seen in fake.requests}) == len(
        fake.requests
    )


def test_a_reused_run_id_with_changed_content_gets_a_new_key(
    fake: FakeLlmModule,
) -> None:
    """run_sync --run-id can be reused. A far side that caches replies by key
    must not answer the edited document with the old document's reply."""
    _sink(fake, run_id="run-1").upsert_document(_record(), _chunks())
    edited = _chunks(text=lambda o: "parandatud lõik" if o == 1 else _text(o))
    _sink(fake, run_id="run-1").upsert_document(_record(), edited)

    first, second = fake.requests
    assert first.key == second.key == "run-1:d1:p0"
    assert first.headers["Idempotency-Key"] != second.headers["Idempotency-Key"]


def test_a_transport_retry_resends_the_same_key(fake: FakeLlmModule) -> None:
    fake.reply_next(Reply(503))

    _sink(fake).upsert_document(_record(), _chunks())

    first, second = fake.requests
    assert first.key == "run-1:d1:p0"
    assert first.headers["Idempotency-Key"] == second.headers["Idempotency-Key"]
    assert first.raw == second.raw


def test_replaying_an_upsert_from_a_later_run_adds_no_points(
    fake: FakeLlmModule,
) -> None:
    _sink(fake, run_id="run-1").upsert_document(_record(), _chunks())
    _sink(fake, run_id="run-2").upsert_document(_record(), _chunks())

    assert len(fake.points) == 3
    assert fake.requests[0].body == fake.requests[1].body


# --------------------------------------------------------------------------
# L8 — metadata-only merge
# --------------------------------------------------------------------------


def test_a_metadata_merge_is_refused_until_the_far_side_agrees(
    fake: FakeLlmModule,
) -> None:
    with pytest.raises(NotSupported, match="supports_metadata_only_merge"):
        _sink(fake).upsert_document(_record(), None)
    assert fake.requests == []


def test_a_metadata_merge_sends_the_record_and_no_chunks(fake: FakeLlmModule) -> None:
    sink = _sink(fake, terms=MERGE_TERMS)
    sink.upsert_document(_record(), _chunks())
    texts = {chunk_id: point["text"] for chunk_id, point in fake.points.items()}

    outcome = sink.upsert_document(_record(title="Õppetoetus 2027"), None)

    assert outcome.status is UpsertStatus.PUBLISHED
    assert outcome.metadata_only
    assert outcome.chunks_written == 0
    body = fake.requests[-1].body
    assert body["chunks"] is None
    assert body["part"] == {"index": 0, "count": 1, "final": True}
    assert fake.records["d1"]["source"]["page_title"] == "Õppetoetus 2027"
    assert {k: p["text"] for k, p in fake.points.items()} == texts
    assert all(
        p["record"]["source"]["page_title"] == "Õppetoetus 2027"
        for p in fake.points.values()
    )


@pytest.mark.parametrize(
    ("status", "times", "expected"),
    [
        (400, 1, UpsertStatus.FAILED_PERMANENT),
        (503, ITEM_RETRY_ROUNDS + 1, UpsertStatus.FAILED_RETRYABLE),
    ],
)
def test_a_failed_metadata_merge_is_not_published(
    fake: FakeLlmModule, status: int, times: int, expected: UpsertStatus
) -> None:
    fake.fail_item("record", status, times=times)

    outcome = _sink(fake, terms=MERGE_TERMS).upsert_document(_record(), None)

    assert outcome.status is expected
    assert outcome.metadata_only


def test_a_retried_metadata_merge_publishes(fake: FakeLlmModule) -> None:
    fake.fail_item("record", 503)

    outcome = _sink(fake, terms=MERGE_TERMS).upsert_document(_record(), None)

    assert outcome.published
    assert [seen.key for seen in fake.requests] == ["run-1:d1:p0", "run-1:d1:p0:r1"]


# --------------------------------------------------------------------------
# L14 — nothing readable leaves through a log or a report
# --------------------------------------------------------------------------


def test_no_document_text_or_token_reaches_a_log_or_a_detail(
    fake: FakeLlmModule, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    fake.fail_item(_id(1), 503)
    fake.reply_next(Reply(429, headers={"Retry-After": "1"}))
    sink = _sink(fake)

    outcomes = [
        sink.upsert_document(_record("d1"), _chunks("d1")),
        _sink(fake, max_request_bytes=300).upsert_document(
            _record("d2"), _chunks("d2")
        ),
        _sink(fake, token_provider=lambda: "").upsert_document(
            _record("d3"), _chunks("d3")
        ),
    ]
    fake.fail_item("record", 400, "lõik 1 on vigane")
    outcomes.append(sink.upsert_document(_record("d4"), _chunks("d4")))

    assert caplog.records
    for leaked in ("lõik", "detsember", "Õppetoetus", TOKEN):
        assert leaked not in caplog.text
        assert all(leaked not in (outcome.detail or "") for outcome in outcomes)


@pytest.mark.parametrize(
    ("code", "shown"),
    [
        ("embedder-busy", "embedder-busy"),
        ("E_TOO_LONG.v2", "E_TOO_LONG.v2"),
        ("section 1 is invalid", "-"),
        ("lõik", "-"),
        ("x" * 41, "-"),
    ],
)
def test_only_a_code_shaped_item_code_reaches_the_report(
    fake: FakeLlmModule, code: str, shown: str
) -> None:
    """An English sentence is as much a leak as an Estonian one: it is
    replaced, not squeezed into something that still reads."""
    fake.fail_item(_id(2), 400, code)

    outcome = _sink(fake).upsert_document(_record(), _chunks())

    assert outcome.detail == f"chunk-400:{shown}"


# --------------------------------------------------------------------------
# The coordinator drives it unchanged
# --------------------------------------------------------------------------


def _coordinator(
    sink: LlmModuleSink,
    source: FakeDocumentSource,
    manifests: InMemoryStore,
    tmp_path: Path,
) -> PublishCoordinator:
    profile = CHUNK_PROFILES["compact"]
    return PublishCoordinator(
        sink=sink,
        manifests=ManifestService(
            ManifestStore(manifests, prefix="manifests"),
            sink_id=sink.sink_id,
            on_missing="first_run",
        ),
        source=source,
        chunker=Chunker.from_profile(profile),
        chunker_fingerprint=chunker_fingerprint(profile),
        work_dir=tmp_path / "work",
        max_chunks_per_document=5000,
        bootstrap_checkpoint_every=250,
        concurrency=4,
        sink_failure_abort_threshold=25,
        run_state=None,
    )


def test_the_coordinator_publishes_through_the_sink_and_falls_back_on_metadata(
    fake: FakeLlmModule, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    source = FakeDocumentSource()
    manifests = InMemoryStore()
    prose = " ".join(
        f"Lause {i} räägib sellest, kuidas taotlus esitada." for i in range(20)
    )
    for document_id in ("d1", "d2", "d3"):
        source.put(make_row(document_id), prose)

    first = _coordinator(_sink(fake, run_id="run-1"), source, manifests, tmp_path)
    report = first.export("run-1")

    assert report.outcome == "success"
    assert set(fake.records) == {"d1", "d2", "d3"}
    assert all(fake.points_of(d) for d in ("d1", "d2", "d3"))
    assert "capped at 2 by the sink" in caplog.text

    # Only d1's sidecar changes. AGREED_TERMS has no metadata merge, so the
    # coordinator re-pushes it in full and says so.
    current = source.documents["d1"]
    source.put(
        replace(current.row, updated_at="2026-10-08T09:00:00Z"),
        current.text,
        sidecar={"source_url": current.row.url, "page_title": "Uus pealkiri"},
    )
    requests_before = len(fake.requests)
    second = _coordinator(_sink(fake, run_id="run-2"), source, manifests, tmp_path)
    report = second.export("run-2")

    assert report.outcome == "success"
    assert report.metadata_full_repush_count == 1
    resent = fake.requests[requests_before:]
    assert [seen.body["document_id"] for seen in resent] == ["d1"]
    assert resent[0].body["chunks"]
    assert fake.records["d1"]["source"]["page_title"] == "Uus pealkiri"
