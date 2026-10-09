"""Tests for content-external's metadata stage (C1-C6)."""

import ast
import dataclasses
import hashlib
import inspect
import json
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from exporter.core import metadata
from exporter.core.metadata import (
    MAX_SIDECAR_BYTES,
    SidecarRejection,
    ascii_identity,
    build_document_record,
    canonical_sidecar_json,
    metadata_sha256,
    parse_sidecar,
    record_identity,
    resolve_source_url,
)
from exporter.core.constants import CHUNK_PROFILES
from exporter.core.diff import classify, record
from exporter.core.ids import chunker_fingerprint
from exporter.core.schemas import (
    DocumentRecord,
    DocumentRef,
    ParsedSidecar,
    to_json_dict,
)

PAGE = "https://www.sotsiaalkindlustusamet.ee/et/toetused/peretoetused"
OTHER_PAGE = "https://www.sotsiaalkindlustusamet.ee/et/pension"
SYNCED_AT = "2026-10-07T08:00:00Z"


def cleaned_sidecar(**overrides: object) -> dict[str, Any]:
    """The shape scrapper/scrapper/items.py:MetadataItem writes, after
    cleaning/worker/tasks.py has set metadata.cleaned and metadata.language."""
    sidecar: dict[str, Any] = {
        "file_type": ".html",
        "source_url": PAGE,
        "metadata": {"cleaned": True, "edited": False, "language": "et"},
        "page_title": "Peretoetused | Sotsiaalkindlustusamet",
        "external_id": "",
        "version": "1.0",
        "created_at": "2026-09-30 08:14:22.123456+00:00",
        "edited_at": None,
    }
    return sidecar | overrides


def edited_sidecar() -> dict[str, Any]:
    """What scrapper/api/app.py:generate_edited_metadata writes from it."""
    sidecar = cleaned_sidecar(edited_at="2026-10-01 09:00:00.000000+00:00")
    sidecar["metadata"] = sidecar["metadata"] | {"edited": True}
    return sidecar


def encode(sidecar: dict[str, Any]) -> bytes:
    return json.dumps(sidecar).encode("utf-8")


def parse(
    data: bytes | None,
    row_url: str | None = PAGE,
    *,
    require_sidecar: bool = True,
) -> ParsedSidecar | SidecarRejection:
    return parse_sidecar(data, row_url=row_url, require_sidecar=require_sidecar)


def parsed_ok(data: bytes | None, row_url: str | None = PAGE) -> ParsedSidecar:
    result = parse(data, row_url)
    assert isinstance(result, ParsedSidecar), result
    return result


def ref(**overrides: object) -> DocumentRef:
    values: dict[str, Any] = {
        "document_id": "0b9f2b4e-6a52-4d8e-9a43-1f3c2d5e7a10",
        "source_base_id": "5c1d3e2f-7b64-4a9d-8e21-6f0a9b8c7d31",
        "agency_id": "a1",
        "url": PAGE,
        "page_title": "Peretoetused",
        "subsector": "",
        "status": "finished",
        "is_excluded": False,
        "is_deleted": False,
        "updated_at": "2026-10-01T09:00:00Z",
        "content_key": "uploads/scrapped-data/x/cleaned.txt",
        "metadata_key": "uploads/scrapped-data/x/cleaned.meta.json",
        "content_origin": "cleaned",
        "file_size": None,
        "raw_sha256": "a" * 64,
        "content_sha256": "b" * 64,
        "metadata_sha256": None,
    }
    return DocumentRef(**(values | overrides))


def build(parsed: ParsedSidecar, **ref_overrides: object) -> DocumentRecord:
    return build_document_record(
        ref(**ref_overrides), parsed, chunk_count=1, synced_at=SYNCED_AT
    )


# --- C1: sidecar parsing -----------------------------------------------------


@pytest.mark.parametrize("sidecar", [cleaned_sidecar(), edited_sidecar()])
def test_real_shaped_sidecars_parse_verbatim(sidecar: dict[str, Any]) -> None:
    parsed = parsed_ok(encode(sidecar))
    assert to_json_dict(parsed.sidecar) == sidecar
    assert parsed.source_url == PAGE
    assert parsed.source_url_origin == "sidecar"
    assert parsed.source_url_mismatch is False


def test_a_utf8_bom_is_tolerated() -> None:
    data = encode(cleaned_sidecar())
    assert parsed_ok(b"\xef\xbb\xbf" + data) == parsed_ok(data)


def test_parsing_takes_bytes_never_a_key_or_path() -> None:
    assert list(inspect.signature(parse_sidecar).parameters) == [
        "data",
        "row_url",
        "require_sidecar",
    ]


def _code_string_constants(tree: ast.Module) -> list[str]:
    """Every string literal in the module except docstrings."""
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)
        ):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_the_module_knows_no_sidecar_filename_or_key_column() -> None:
    """C1: the key comes from the source_file row, resolved upstream into
    DocumentRef.metadata_key. No code path here can name a sidecar file or
    pick between the two key columns itself."""
    source = Path(metadata.__file__).read_text(encoding="utf-8")
    literals = _code_string_constants(ast.parse(source))
    assert literals, "the collector found no literals at all"
    offending = [
        s
        for s in literals
        if ".meta.json" in s or "metadata_url" in s or "metadata_key" in s
    ]
    assert not offending


def test_parsed_sidecar_is_frozen_and_copied() -> None:
    parsed = parsed_ok(encode(cleaned_sidecar()))
    with pytest.raises(TypeError):
        parsed.sidecar["metadata"]["cleaned"] = False  # type: ignore[index]

    mine = cleaned_sidecar()
    built = ParsedSidecar(
        sidecar=mine,
        metadata_sha256=metadata_sha256(mine),
        source_url=PAGE,
        source_url_origin="sidecar",
        source_url_mismatch=False,
    )
    mine["page_title"] = "changed"
    mine["metadata"]["language"] = "en"
    assert built.sidecar["page_title"] == cleaned_sidecar()["page_title"]
    assert built.sidecar["metadata"]["language"] == "et"


def test_non_nfc_text_stays_verbatim() -> None:
    """Decision: the sidecar is not normalised. A decomposed title stays
    decomposed and hashes differently from the composed form."""
    decomposed = unicodedata.normalize("NFD", "Õppetoetus")
    composed = unicodedata.normalize("NFC", "Õppetoetus")
    assert decomposed != composed
    parsed = parsed_ok(encode(cleaned_sidecar(page_title=decomposed)))
    assert parsed.sidecar["page_title"] == decomposed
    assert parsed.metadata_sha256 != metadata_sha256(
        cleaned_sidecar(page_title=composed)
    )


# --- C5: missing / invalid sidecars skip, never raise ------------------------


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (None, SidecarRejection.MISSING),
        (b"\xff\xfe{}", SidecarRejection.UNDECODABLE),
        (b"", SidecarRejection.INVALID_JSON),
        (b"{", SidecarRejection.INVALID_JSON),
        (b'{"a": NaN}', SidecarRejection.INVALID_JSON),
        (b'{"a": -Infinity}', SidecarRejection.INVALID_JSON),
        (b'{"a": 1e999}', SidecarRejection.INVALID_JSON),
        (b'{"a": "\\ud800"}', SidecarRejection.INVALID_JSON),
        (b'{"\\ud800": 1}', SidecarRejection.INVALID_JSON),
        (b'{"a": ' + b"9" * 5000 + b"}", SidecarRejection.INVALID_JSON),
        (b"[" * 100_000 + b"]" * 100_000, SidecarRejection.INVALID_JSON),
        (
            b'{"a": ' + b"[" * 40 + b"]" * 40 + b"}",
            SidecarRejection.INVALID_JSON,
        ),
        (b'{"a": 1, "a": 2}', SidecarRejection.INVALID_JSON),
        (b'{"m": {"x": 1, "x": 1}}', SidecarRejection.INVALID_JSON),
        (b"[]", SidecarRejection.NOT_AN_OBJECT),
        (b'"x"', SidecarRejection.NOT_AN_OBJECT),
        (b"null", SidecarRejection.NOT_AN_OBJECT),
        (b"42", SidecarRejection.NOT_AN_OBJECT),
    ],
    ids=lambda v: repr(v[:24]) if isinstance(v, bytes) else repr(v),
)
def test_bad_sidecars_return_a_reason_and_never_raise(
    data: bytes | None, reason: SidecarRejection
) -> None:
    assert parse(data) is reason


def test_an_oversized_sidecar_is_rejected_before_parsing() -> None:
    padding = b"x" * MAX_SIDECAR_BYTES
    data = b'{"source_url": "' + PAGE.encode() + b'", "pad": "' + padding + b'"}'
    assert parse(data) is SidecarRejection.TOO_LARGE
    # Over the cap, even when the bytes are not JSON at all.
    assert parse(b"\xff" * (MAX_SIDECAR_BYTES + 1)) is SidecarRejection.TOO_LARGE


def test_a_sidecar_at_the_cap_is_accepted() -> None:
    head = b'{"source_url": "' + PAGE.encode() + b'", "pad": "'
    data = head + b"x" * (MAX_SIDECAR_BYTES - len(head) - 2) + b'"}'
    assert len(data) == MAX_SIDECAR_BYTES
    assert isinstance(parse(data), ParsedSidecar)


def test_rejection_reasons_are_stable_report_strings() -> None:
    assert {r.value for r in SidecarRejection} == {
        "sidecar_missing",
        "sidecar_too_large",
        "sidecar_undecodable",
        "sidecar_invalid_json",
        "sidecar_not_an_object",
        "invalid_source_url",
        "uncitable_source_url",
    }


def test_a_missing_sidecar_is_allowed_when_not_required() -> None:
    result = parse(None, require_sidecar=False)
    assert isinstance(result, ParsedSidecar)
    assert result.sidecar == {}
    assert result.metadata_sha256 == hashlib.sha256(b"{}").hexdigest()
    assert (result.source_url, result.source_url_origin) == (PAGE, "row")


def test_a_missing_sidecar_still_needs_a_citable_row_url() -> None:
    assert (
        parse(None, row_url=None, require_sidecar=False)
        is SidecarRejection.INVALID_SOURCE_URL
    )


def test_not_requiring_a_sidecar_does_not_excuse_a_corrupt_one() -> None:
    assert parse(b"{", require_sidecar=False) is SidecarRejection.INVALID_JSON


def test_moderate_nesting_is_accepted() -> None:
    data = b'{"source_url": "' + PAGE.encode() + b'", "a": [[[{"b": [1]}]]]}'
    assert isinstance(parse(data), ParsedSidecar)


# --- C2: source_url validation and fallback ----------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "",
        "   ",
        42,
        ["https://x.ee"],
        "/et/toetused",
        "www.example.ee/page",
        "ftp://example.ee/file.pdf",
        "javascript:alert(1)",
        "mailto:info@example.ee",
        "https://",
        "https:///path-only",
        "https://exa mple.ee/",
        "https://example.ee/a\tb",
        "https://example.ee/\x00",
        "https://[::1/",
        "https://example.ee:notaport/",
        "https://example.ee:99999/",
        "https://example.ee/" + "a" * 2048,
    ],
    ids=lambda v: repr(v)[:40],
)
def test_an_unusable_sidecar_url_falls_back_to_the_row(bad: object) -> None:
    parsed = parsed_ok(encode(cleaned_sidecar(source_url=bad)), row_url=OTHER_PAGE)
    assert (parsed.source_url, parsed.source_url_origin) == (OTHER_PAGE, "row")
    assert parsed.source_url_mismatch is False


def test_a_url_at_the_length_cap_is_accepted() -> None:
    url = "https://example.ee/" + "a" * (2048 - len("https://example.ee/"))
    assert len(url) == 2048
    assert not isinstance(resolve_source_url(url, None), SidecarRejection)


def test_a_sidecar_with_no_source_url_key_falls_back_to_the_row() -> None:
    sidecar = cleaned_sidecar()
    del sidecar["source_url"]
    parsed = parsed_ok(encode(sidecar))
    assert (parsed.source_url, parsed.source_url_origin) == (PAGE, "row")


def test_the_fallback_does_not_rewrite_the_sidecar() -> None:
    parsed = parsed_ok(encode(cleaned_sidecar(source_url="")))
    assert parsed.sidecar["source_url"] == ""
    assert parsed.source_url == PAGE


def test_neither_url_usable_is_invalid() -> None:
    assert (
        parse(encode(cleaned_sidecar(source_url="")), row_url=None)
        is SidecarRejection.INVALID_SOURCE_URL
    )


def test_two_valid_urls_that_differ_keep_the_sidecar_and_flag_it() -> None:
    parsed = parsed_ok(encode(cleaned_sidecar()), row_url=OTHER_PAGE)
    assert (parsed.source_url, parsed.source_url_origin) == (PAGE, "sidecar")
    assert parsed.source_url_mismatch is True


@pytest.mark.parametrize(
    "row_url",
    [
        "HTTPS://WWW.Sotsiaalkindlustusamet.EE/et/toetused/peretoetused",
        PAGE + "/",
        PAGE.replace(".ee/", ".ee:443/", 1),
        PAGE + "#taotlemine",
    ],
    ids=["case", "trailing-slash", "default-port", "fragment"],
)
def test_cosmetic_differences_are_not_a_mismatch(row_url: str) -> None:
    parsed = parsed_ok(encode(cleaned_sidecar()), row_url=row_url)
    assert parsed.source_url == PAGE
    assert parsed.source_url_mismatch is False


@pytest.mark.parametrize(
    "row_url",
    [
        PAGE + "/taotlus",
        PAGE + "?lang=en",
        PAGE.replace("https://", "http://", 1),
        PAGE.replace(".ee/", ".ee:8443/", 1),
    ],
    ids=["path", "query", "scheme", "port"],
)
def test_real_differences_are_a_mismatch(row_url: str) -> None:
    assert parsed_ok(encode(cleaned_sidecar()), row_url=row_url).source_url_mismatch


def test_scheme_case_is_ignored_and_whitespace_is_stripped() -> None:
    resolved = resolve_source_url("  HTTPS://www.example.ee/teenus \n", None)
    assert not isinstance(resolved, SidecarRejection)
    assert resolved.url == "HTTPS://www.example.ee/teenus"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.riigiteataja.ee/akt/õigusakt",
        "https://õigus.ee/akt",
        "https://[2001:db8::1]/a",
    ],
)
def test_unicode_and_ipv6_urls_are_citable(url: str) -> None:
    resolved = resolve_source_url(url, None)
    assert not isinstance(resolved, SidecarRejection)
    assert resolved.url == url


# The uploaded-file case: the scrapper records file-processing's presigned
# download link as both the sidecar's source_url and the row's url.
SIGNED_URLS = [
    "https://s3.example.ee/ckb/uploads/a.pdf?X-Amz-Algorithm=AWS4-HMAC-SHA256"
    "&X-Amz-Credential=AKIA%2F20261001%2Feu-north-1%2Fs3%2Faws4_request"
    "&X-Amz-Date=20261001T000000Z&X-Amz-Expires=3600&X-Amz-SignedHeaders=host"
    "&X-Amz-Signature=abc123",
    "https://bucket.s3.amazonaws.com/a.pdf?AWSAccessKeyId=AKIA&Expires=1&Signature=x",
    "https://acct.blob.core.windows.net/c/a.pdf?sv=2024-01-01&se=2026-10-02&sp=r&sig=x",
    "https://storage.googleapis.com/b/a.pdf?X-Goog-Signature=x",
    "https://user:secret@example.ee/a.pdf",
    "https://example.ee/a.pdf?x-amz-SIGNATURE=abc",
    "https://example.ee/a.pdf?%73ig=abc",
    "https://example.ee/a.pdf?a=1;sig=abc",
    "https://example.ee/a.pdf#frag?sig=abc",
    "https://example.ee/a.pdf#x&X-Amz-Signature=abc",
]


@pytest.mark.parametrize("signed", SIGNED_URLS, ids=lambda u: u[:48])
def test_a_signed_url_is_uncitable(signed: str) -> None:
    assert resolve_source_url(signed, None) is SidecarRejection.UNCITABLE_SOURCE_URL
    assert resolve_source_url(None, signed) is SidecarRejection.UNCITABLE_SOURCE_URL


@pytest.mark.parametrize("signed", SIGNED_URLS, ids=lambda u: u[:48])
def test_a_signed_sidecar_url_skips_even_with_a_clean_row_url(signed: str) -> None:
    """The sidecar is published verbatim under `source`, so a clean fallback
    citation would still ship the signed URL."""
    assert (
        parse(encode(cleaned_sidecar(source_url=signed)), row_url=PAGE)
        is SidecarRejection.UNCITABLE_SOURCE_URL
    )


@pytest.mark.parametrize("signed", SIGNED_URLS, ids=lambda u: u[:48])
def test_a_signed_row_url_is_never_published(signed: str) -> None:
    """With a clean sidecar the row's url is only a cross-check: unusable,
    so not a mismatch, and absent from everything that leaves the service."""
    parsed = parsed_ok(encode(cleaned_sidecar()), row_url=signed)
    assert (parsed.source_url, parsed.source_url_mismatch) == (PAGE, False)
    published = json.dumps(to_json_dict(build(parsed)), ensure_ascii=False)
    assert signed not in published


@pytest.mark.parametrize(
    "url",
    [
        "https://www.example.ee/index.php?sid=12&session=x&auth=public&id=3",
        "https://www.example.ee/teenus#sig",
        "https://www.example.ee/teenus#section-2",
        "https://www.example.ee/teenus;jsessionid=1",
        "https://www.example.ee/signature-guide?topic=sig",
    ],
)
def test_ordinary_urls_are_not_mistaken_for_signed_ones(url: str) -> None:
    """redaction.py would redact some of these, and some look like signed
    parameters until parsed. None is a reason to skip."""
    assert not isinstance(resolve_source_url(url, None), SidecarRejection)


# --- C3: canonical metadata hash ---------------------------------------------


def test_key_reordering_does_not_move_the_hash() -> None:
    sidecar = cleaned_sidecar()
    reordered = dict(reversed(list(sidecar.items())))
    reordered["metadata"] = dict(reversed(list(sidecar["metadata"].items())))
    assert list(reordered) != list(sidecar)
    assert metadata_sha256(reordered) == metadata_sha256(sidecar)


def test_reserialising_the_bytes_does_not_move_the_hash() -> None:
    sidecar = cleaned_sidecar(page_title="Õppetoetus")
    compact = json.dumps(sidecar, separators=(",", ":")).encode()
    pretty = json.dumps(sidecar, indent=4, ensure_ascii=False).encode()
    assert compact != pretty
    assert parsed_ok(compact).metadata_sha256 == parsed_ok(pretty).metadata_sha256


@pytest.mark.parametrize(
    "change",
    [
        {"page_title": "Peretoetused (uuendatud)"},
        {"metadata": {"cleaned": True, "edited": False, "language": "en"}},
        {"edited_at": "2026-10-02 10:00:00+00:00"},
    ],
    ids=["page_title", "metadata.language", "edited_at"],
)
def test_a_value_change_moves_the_hash(change: dict[str, Any]) -> None:
    assert metadata_sha256(cleaned_sidecar(**change)) != metadata_sha256(
        cleaned_sidecar()
    )


def test_the_hash_is_over_utf8_estonian_not_escapes() -> None:
    sidecar = {"page_title": "Õppetoetus šžõäöü", "a": 1}
    expected = '{"a": 1, "page_title": "Õppetoetus šžõäöü"}'
    assert canonical_sidecar_json(sidecar) == expected
    assert metadata_sha256(sidecar) == hashlib.sha256(expected.encode()).hexdigest()


def test_a_frozen_sidecar_hashes_like_the_plain_dict() -> None:
    parsed = parsed_ok(encode(cleaned_sidecar()))
    assert metadata_sha256(parsed.sidecar) == metadata_sha256(cleaned_sidecar())
    assert parsed.metadata_sha256 == metadata_sha256(cleaned_sidecar())


def test_the_hash_ignores_which_url_was_resolved() -> None:
    """Covers the sidecar only, per C3, not the fallback citation."""
    data = encode(cleaned_sidecar(source_url=""))
    assert (
        parsed_ok(data, row_url=PAGE).metadata_sha256
        == parsed_ok(data, row_url=OTHER_PAGE).metadata_sha256
    )


@pytest.mark.parametrize(
    ("sidecar", "error"),
    [
        ({"a": float("nan")}, ValueError),
        ({"\ud800": 1}, UnicodeEncodeError),
    ],
)
def test_the_hash_functions_raise_on_input_the_loader_would_refuse(
    sidecar: dict[str, Any], error: type[Exception]
) -> None:
    """Documented: canonical_sidecar_json and metadata_sha256 expect a sidecar
    parse_sidecar() accepted. parse_sidecar itself turns these into reasons."""
    with pytest.raises(error):
        metadata_sha256(sidecar)


# --- C4: the canonical document record ---------------------------------------


def test_the_record_carries_exactly_the_canonical_fields() -> None:
    parsed = parsed_ok(encode(edited_sidecar()))
    record = build_document_record(
        ref(content_origin="edited"), parsed, chunk_count=3, synced_at=SYNCED_AT
    )
    loaded = json.loads(json.dumps(to_json_dict(record), ensure_ascii=False))
    assert loaded == {
        "document_id": "0b9f2b4e-6a52-4d8e-9a43-1f3c2d5e7a10",
        "source_base_id": "5c1d3e2f-7b64-4a9d-8e21-6f0a9b8c7d31",
        "content_origin": "edited",
        "source_url": PAGE,
        "source": edited_sidecar(),
        "raw_sha256": "a" * 64,
        "content_sha256": "b" * 64,
        "metadata_sha256": parsed.metadata_sha256,
        "chunk_count": 3,
        "synced_at": SYNCED_AT,
    }


def test_the_record_takes_the_resolved_url_not_the_sidecars() -> None:
    parsed = parsed_ok(encode(cleaned_sidecar(source_url="")), row_url=OTHER_PAGE)
    record = build(parsed)
    assert record.source_url == OTHER_PAGE
    assert record.source["source_url"] == ""


def test_a_matching_metadata_hash_on_the_ref_is_accepted() -> None:
    parsed = parsed_ok(encode(cleaned_sidecar()))
    record = build_document_record(
        ref(metadata_sha256=parsed.metadata_sha256),
        parsed,
        chunk_count=0,
        synced_at="2026-10-07T08:00:00.123456+03:00",
    )
    assert record.chunk_count == 0


@pytest.mark.parametrize(
    ("ref_overrides", "kwargs"),
    [
        ({"content_origin": None}, {}),
        ({"content_origin": "raw"}, {}),
        ({"raw_sha256": None}, {}),
        ({"content_sha256": None}, {}),
        ({"metadata_sha256": "f" * 64}, {}),
        ({}, {"chunk_count": -1}),
        ({}, {"chunk_count": True}),
        ({}, {"chunk_count": 1.0}),
        ({}, {"synced_at": ""}),
        ({}, {"synced_at": "t"}),
        ({}, {"synced_at": "2026-10-07"}),
        ({}, {"synced_at": "2026-10-07T08:00:00"}),
        ({}, {"synced_at": "2026-10-07 08:00:00Z"}),
    ],
)
def test_an_upstream_bug_raises(
    ref_overrides: dict[str, object], kwargs: dict[str, object]
) -> None:
    parsed = parsed_ok(encode(cleaned_sidecar()))
    arguments: dict[str, Any] = {"chunk_count": 1, "synced_at": SYNCED_AT} | kwargs
    with pytest.raises(ValueError):
        build_document_record(ref(**ref_overrides), parsed, **arguments)


def hand_built(sidecar: dict[str, Any], **overrides: object) -> ParsedSidecar:
    values: dict[str, Any] = {
        "sidecar": sidecar,
        "metadata_sha256": metadata_sha256(sidecar),
        "source_url": PAGE,
        "source_url_origin": "sidecar",
        "source_url_mismatch": False,
    }
    return ParsedSidecar(**(values | overrides))


@pytest.mark.parametrize(
    "parsed",
    [
        hand_built(cleaned_sidecar(), source_url=SIGNED_URLS[0]),
        hand_built(cleaned_sidecar(), source_url="not a url"),
        hand_built(cleaned_sidecar(), source_url=" " + PAGE),
        hand_built(cleaned_sidecar(), source_url="invalid_source_url"),
        hand_built(cleaned_sidecar(source_url=SIGNED_URLS[0])),
        hand_built(cleaned_sidecar(), metadata_sha256="0" * 64),
    ],
    ids=[
        "signed-citation",
        "invalid-citation",
        "unstripped-citation",
        "rejection-string-as-citation",
        "signed-url-in-sidecar",
        "wrong-hash",
    ],
)
def test_a_parsed_sidecar_parse_sidecar_would_not_produce_raises(
    parsed: ParsedSidecar,
) -> None:
    with pytest.raises(ValueError):
        build(parsed)


def test_record_identity_is_the_fixed_chunk_subset() -> None:
    record = build(parsed_ok(encode(cleaned_sidecar())))
    assert record_identity(record) == {
        "document_id": record.document_id,
        "source_base_id": record.source_base_id,
        "content_origin": "cleaned",
        "source_url": PAGE,
    }


# --- C6: ASCII-safe identity ---------------------------------------------------


def estonian_record() -> DocumentRecord:
    sidecar = cleaned_sidecar(
        page_title="Õppetoetuse taotlemine – šžõäöü",
        source_url="https://www.riigiteataja.ee/akt/õigusakt",
        external_id="ÕÄÖÜ-1",
    )
    parsed = parsed_ok(encode(sidecar))
    return build_document_record(ref(), parsed, chunk_count=12, synced_at=SYNCED_AT)


def test_ascii_identity_is_exactly_ids_hashes_origin_and_count() -> None:
    record = estonian_record()
    assert ascii_identity(record) == {
        "document_id": "0b9f2b4e-6a52-4d8e-9a43-1f3c2d5e7a10",
        "source_base_id": "5c1d3e2f-7b64-4a9d-8e21-6f0a9b8c7d31",
        "content_origin": "cleaned",
        "raw_sha256": "a" * 64,
        "content_sha256": "b" * 64,
        "metadata_sha256": record.metadata_sha256,
        "chunk_count": "12",
    }
    assert all(value.isascii() for value in ascii_identity(record).values())


@pytest.mark.parametrize(
    "overrides",
    [
        {"document_id": "dokument-õ"},
        {"document_id": "d1\r\nX-Injected: 1"},
        {"document_id": "d1 d2"},
        {"document_id": ""},
        {"source_base_id": "-leading-dash"},
        {"raw_sha256": "A" * 64},
        {"content_sha256": "b" * 63},
        {"metadata_sha256": "not-a-hash"},
        {"content_origin": "raw"},
        {"chunk_count": -1},
        {"chunk_count": True},
    ],
    ids=repr,
)
def test_ascii_identity_refuses_anything_off_shape(
    overrides: dict[str, object],
) -> None:
    record = dataclasses.replace(estonian_record(), **overrides)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ascii_identity(record)


# --- the published record and the manifest entry agree ----------------------


def test_the_published_record_and_the_manifest_entry_agree() -> None:
    """Two shapes are built from one read row: the record the destination
    receives and the entry the next diff compares against. Wherever they
    share a field they must agree, or the next run diffs against something
    that was never published."""
    parsed = parsed_ok(encode(cleaned_sidecar()))
    row = ref(metadata_sha256=parsed.metadata_sha256, file_size=48213)
    fingerprint = chunker_fingerprint(CHUNK_PROFILES["azure_native"])
    result = classify(None, [row], fingerprint).with_chunk_tails({row.document_id: 12})
    entry = record(
        result, {row.document_id}, tails_deleted=set(), processed_at=SYNCED_AT
    )[row.document_id]
    published = build_document_record(
        row,
        parsed,
        chunk_count=result.chunk_counts[row.document_id],
        synced_at=SYNCED_AT,
    )
    shared = (
        "source_base_id",
        "content_origin",
        "raw_sha256",
        "content_sha256",
        "metadata_sha256",
        "chunk_count",
    )
    assert {name: getattr(entry, name) for name in shared} == {
        name: getattr(published, name) for name in shared
    }
