"""Sidecar parsing, citation URL, canonical metadata hash and the canonical
document record. Stage C.1 (C1-C6), Key Design #4.

Pure: no I/O, no clock, no settings. The caller passes in the sidecar's
bytes, the row's url, REQUIRE_METADATA_SIDECAR and synced_at.

Where the sidecar comes from (C1). cleaned.meta.json is written by
cleaning/worker/tasks.py; edited.meta.json by the scrapper's
generate-edited-metadata when a curator edits. Both start from the scrapper's
MetadataItem: file_type, source_url, page_title, external_id, version,
created_at, edited_at, metadata{cleaned, edited, language}. Which of the two
applies is decided by the source_file row (edited_metadata_url, falling back
to cleaned_metadata_url) and resolved upstream into DocumentRef.metadata_key.
parse_sidecar() takes bytes and nothing else, so it cannot build a key from a
filename convention or a config guess, even by accident.

Never raise on sidecar data (C5). A missing or invalid sidecar is a
SidecarRejection returned to the caller, which skips, counts and reports the
document. One bad sidecar must not stop an agency's other documents.
REQUIRE_METADATA_SIDECAR relaxes absence only: a sidecar that exists but
does not parse is always rejected, because that is a signal something
upstream broke. So is one over MAX_SIDECAR_BYTES, and one with a duplicate
key, which JSON parsers resolve differently and which the hash would hide.

The citation (C2). source_url is what a retrieval answer cites, so a document
with no usable one is skipped. The sidecar's value wins; source_file.url is
the fallback and the cross-check. The resolved value goes in a top-level
DocumentRecord.source_url and the sidecar itself is never rewritten.

A presigned or credential-carrying URL is "uncitable", and uncitable means
unpublished. A signed URL in the sidecar therefore skips the document even
when the row's url is clean: the sidecar is published verbatim under
`source`, so falling back would cite a clean URL while still shipping the
signed one. A signed URL in the row only matters when the row is the
fallback, because the row's url is never published otherwise. The
uploaded-file case is both: create_uploaded_source_files.sql inserts no url,
and the scrapper then records the file-processing presigned download link in
the sidecar and the row alike. That link expires and carries a signature.

Signed parameters are looked for in the query and the fragment, split on
"&", ";" and "?" — parse_qsl splits on "&" only, while some servers also
accept ";". The parameter set is deliberately narrow and separate from
redaction.py's list. That list also redacts sid, session and auth, which
appear in ordinary public CMS URLs and would wrongly skip real documents.

The hash (C3) is over the canonical JSON of the parsed sidecar, never its raw
bytes, so an upstream key reorder or reserialisation is not a change. It
covers the sidecar only, not the resolved URL. The fallback is used only when
the sidecar has no usable URL, and a row's url is in practice its identity,
so the two do not drift apart unnoticed.

The sidecar is kept verbatim: it is not NFC-normalised, and numbers keep
their JSON form, so 1 and 1.0 hash differently. If an upstream title flips
between composed and decomposed forms, the cost is one metadata_changed,
which is a small write and does not re-chunk.

The record (C4) is built once, here, and both destinations take it as is.
Neither builds its own shape. record_identity() is the identifying subset
repeated on each chunk. ascii_identity() (C6) is the subset that is safe for
a transport field limited to ASCII. Each destination enforces its own
transport limits; this module only guarantees that the safe subset is the
only thing it offers for that purpose.
"""

import json
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, NamedTuple, NoReturn
from urllib.parse import unquote_plus, urlsplit

from exporter.core.schemas import (
    DocumentRecord,
    DocumentRef,
    ParsedSidecar,
    to_json_dict,
)
from exporter.core.text_normaliser import sha256_text


class SidecarRejection(StrEnum):
    """Why a document's metadata is unusable. The values are stable strings
    for RunReport.skipped_reasons.

    A StrEnum member is also a str, so callers must test with
    isinstance(result, SidecarRejection), not isinstance(result, str).
    """

    MISSING = "sidecar_missing"
    TOO_LARGE = "sidecar_too_large"
    UNDECODABLE = "sidecar_undecodable"
    INVALID_JSON = "sidecar_invalid_json"
    NOT_AN_OBJECT = "sidecar_not_an_object"
    INVALID_SOURCE_URL = "invalid_source_url"
    UNCITABLE_SOURCE_URL = "uncitable_source_url"


class ResolvedSourceUrl(NamedTuple):
    url: str
    origin: str  # "sidecar" | "row"
    mismatch: bool  # both valid and different; the sidecar's was kept


# Real sidecars are well under 1 KB. The cap stops a runaway one from being
# parsed and then published verbatim into a record. Public so the caller can
# refuse before reading the body, the same way MAX_DOCUMENT_BYTES is applied
# to the content object.
MAX_SIDECAR_BYTES = 1_048_576

# Longer than any real citation. Past it a "URL" is more likely a data blob
# or an injected payload than a page someone could follow.
_MAX_URL_LENGTH = 2048

_CITABLE_SCHEMES = frozenset({"http", "https"})
_DEFAULT_PORTS = {"http": 80, "https": 443}

# Parameter names, lowercased, that mark a URL as presigned or as carrying a
# credential. An expiring link is not a citation.
#   S3 SigV4:  X-Amz-Signature, X-Amz-Credential, X-Amz-Security-Token
#   S3 SigV2:  Signature, AWSAccessKeyId
#   Azure SAS: sig
#   GCS V4:    X-Goog-Signature, X-Goog-Credential
_SIGNED_URL_PARAMS = frozenset(
    {
        "x-amz-signature",
        "x-amz-credential",
        "x-amz-security-token",
        "signature",
        "awsaccesskeyid",
        "sig",
        "x-goog-signature",
        "x-goog-credential",
    }
)

_PARAM_SEPARATORS = re.compile(r"[&;?]")

# Any whitespace or control character left after strip(). A URL never
# legitimately contains a raw one.
_SPACE_OR_CONTROL = re.compile(r"[\s\x00-\x1f\x7f]")

# Real sidecars nest two levels deep. The limit stops a pathological sidecar
# from reaching the recursive freeze and serialise steps, which would raise
# RecursionError instead of returning a rejection.
_MAX_SIDECAR_DEPTH = 32

_CONTENT_ORIGINS = frozenset({"edited", "cleaned"})

# synced_at as the rest of the design writes timestamps: seconds precision or
# finer, with an explicit offset.
_ISO_8601_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})"
)

# What ascii_identity() lets through. Narrower than ASCII: no space, no
# control character (a CR or LF would end a header line), nothing a
# transport could reinterpret.
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}")


# --- C1 / C5: parsing ---------------------------------------------------------


def parse_sidecar(
    data: bytes | None, *, row_url: str | None, require_sidecar: bool
) -> ParsedSidecar | SidecarRejection:
    """The sidecar's bytes, already fetched by the caller, become a
    ParsedSidecar or the reason the document must be skipped. Never raises.

    data is None means there is no sidecar. With require_sidecar off, the
    document goes ahead with an empty sidecar and must take its citation
    from row_url.
    """
    if data is None:
        if require_sidecar:
            return SidecarRejection.MISSING
        sidecar: dict[str, object] = {}
    else:
        loaded = _load(data)
        if isinstance(loaded, SidecarRejection):
            return loaded
        sidecar = loaded

    try:
        digest = metadata_sha256(sidecar)
    except (ValueError, UnicodeEncodeError, RecursionError):
        # Infinity from an out-of-range number, or a lone surrogate escape
        # that cannot be encoded as UTF-8.
        return SidecarRejection.INVALID_JSON

    resolved = resolve_source_url(sidecar.get("source_url"), row_url)
    if isinstance(resolved, SidecarRejection):
        return resolved
    return ParsedSidecar(
        sidecar=sidecar,
        metadata_sha256=digest,
        source_url=resolved.url,
        source_url_origin=resolved.origin,
        source_url_mismatch=resolved.mismatch,
    )


def _reject_constant(name: str) -> NoReturn:
    raise ValueError(f"non-standard JSON constant {name}")


def _object_without_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    obj = dict(pairs)
    if len(obj) != len(pairs):
        raise ValueError("duplicate key in JSON object")
    return obj


def _load(data: bytes) -> dict[str, object] | SidecarRejection:
    if len(data) > MAX_SIDECAR_BYTES:
        return SidecarRejection.TOO_LARGE
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return SidecarRejection.UNDECODABLE
    try:
        loaded = json.loads(
            text,
            parse_constant=_reject_constant,
            object_pairs_hook=_object_without_duplicate_keys,
        )
    except (ValueError, RecursionError):
        return SidecarRejection.INVALID_JSON
    if not isinstance(loaded, dict):
        return SidecarRejection.NOT_AN_OBJECT
    if _deeper_than(loaded, _MAX_SIDECAR_DEPTH):
        return SidecarRejection.INVALID_JSON
    return loaded


def _deeper_than(value: object, limit: int) -> bool:
    """Whether containers nest more than `limit` levels. Iterative, so the
    check itself cannot recurse too deeply."""
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, dict):
            children: list[object] = list(item.values())
        elif isinstance(item, list):
            children = item
        else:
            continue
        if depth > limit:
            return True
        stack.extend((child, depth + 1) for child in children)
    return False


# --- C2: the citation ---------------------------------------------------------


def resolve_source_url(
    sidecar_value: object, row_url: str | None
) -> ResolvedSourceUrl | SidecarRejection:
    """The URL a retrieval answer cites: the sidecar's source_url if usable,
    else source_file.url.

    A signed sidecar URL is UNCITABLE_SOURCE_URL whatever the row holds,
    because the sidecar is published verbatim. When both are unusable and
    the row's was signed, the rejection is also UNCITABLE_SOURCE_URL rather
    than INVALID_SOURCE_URL, so the report says why.
    """
    from_sidecar = _check_url(sidecar_value)
    if from_sidecar is SidecarRejection.UNCITABLE_SOURCE_URL:
        return SidecarRejection.UNCITABLE_SOURCE_URL
    from_row = _check_url(row_url)
    if not isinstance(from_sidecar, SidecarRejection):
        mismatch = not isinstance(from_row, SidecarRejection) and (
            _comparable(from_row) != _comparable(from_sidecar)
        )
        return ResolvedSourceUrl(from_sidecar, "sidecar", mismatch)
    if not isinstance(from_row, SidecarRejection):
        return ResolvedSourceUrl(from_row, "row", False)
    if from_row is SidecarRejection.UNCITABLE_SOURCE_URL:
        return SidecarRejection.UNCITABLE_SOURCE_URL
    return SidecarRejection.INVALID_SOURCE_URL


def _check_url(value: object) -> str | SidecarRejection:
    """The stripped URL if it is usable as a citation, else why not."""
    if not isinstance(value, str):
        return SidecarRejection.INVALID_SOURCE_URL
    url = value.strip()
    if not url or len(url) > _MAX_URL_LENGTH or _SPACE_OR_CONTROL.search(url):
        return SidecarRejection.INVALID_SOURCE_URL
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        has_userinfo = parts.username is not None or parts.password is not None
        _ = parts.port  # raises ValueError on a non-numeric or out-of-range port
    except ValueError:
        return SidecarRejection.INVALID_SOURCE_URL
    if parts.scheme.lower() not in _CITABLE_SCHEMES or not hostname:
        return SidecarRejection.INVALID_SOURCE_URL
    if has_userinfo or _param_names(parts.query, parts.fragment) & _SIGNED_URL_PARAMS:
        return SidecarRejection.UNCITABLE_SOURCE_URL
    return url


def _param_names(*components: str) -> set[str]:
    """Lowercased, percent-decoded names of every name=value pair in the
    given URL components. A piece with no "=" is not a parameter: in a
    fragment it is an anchor, such as #sig, and must not skip a document."""
    names: set[str] = set()
    for component in components:
        for piece in _PARAM_SEPARATORS.split(component):
            name, separator, _ = piece.partition("=")
            if separator:
                names.add(unquote_plus(name).strip().lower())
    return names


def _comparable(url: str) -> tuple[str, str, str, str, str]:
    """A validated URL reduced so that only a real difference counts as a
    mismatch: the scheme and host are lowercased, a default port and a
    trailing slash are dropped, and the fragment is ignored."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    port = parts.port
    shown_port = "" if port in (None, _DEFAULT_PORTS[scheme]) else str(port)
    return (
        scheme,
        parts.hostname or "",
        shown_port,
        parts.path.rstrip("/"),
        parts.query,
    )


# --- C3: the canonical hash ---------------------------------------------------


def canonical_sidecar_json(sidecar: Mapping[str, Any]) -> str:
    """The one serialisation metadata_sha256 is computed over: sorted keys,
    Estonian kept as UTF-8 instead of \\u escapes. allow_nan=False changes
    nothing for valid JSON; it only refuses Infinity, which json.loads
    produces from an out-of-range number such as 1e999.

    Expects a sidecar parse_sidecar() has already accepted. Given anything
    else it can raise ValueError (NaN or Infinity), UnicodeEncodeError (a
    lone surrogate, which only the final UTF-8 encode notices) or
    RecursionError (pathological nesting). parse_sidecar() catches all three.
    """
    return json.dumps(
        to_json_dict(sidecar), sort_keys=True, ensure_ascii=False, allow_nan=False
    )


def metadata_sha256(sidecar: Mapping[str, Any]) -> str:
    """Hash of the canonical JSON, never of the sidecar's raw bytes. Same
    input contract and the same exceptions as canonical_sidecar_json()."""
    return sha256_text(canonical_sidecar_json(sidecar))


# --- C4: the canonical record -------------------------------------------------


def build_document_record(
    ref: DocumentRef, parsed: ParsedSidecar, *, chunk_count: int, synced_at: str
) -> DocumentRecord:
    """The canonical document record: the one shape every destination takes.

    Raises ValueError only on a bug upstream: a ref that was never hashed,
    a ParsedSidecar that parse_sidecar() would not have produced (an
    unusable citation, a signed URL in the sidecar, a hash that does not
    match it), an impossible chunk count or a malformed timestamp. Sidecar
    problems never get this far; they were already returned by
    parse_sidecar(). The checks are repeated here because this is the last
    point before the record leaves the service.
    """
    document_id = ref.document_id
    if ref.content_origin not in _CONTENT_ORIGINS:
        raise ValueError(
            f"document {document_id}: content_origin must be one of "
            f"{sorted(_CONTENT_ORIGINS)}, got {ref.content_origin!r}"
        )
    if ref.raw_sha256 is None or ref.content_sha256 is None:
        raise ValueError(
            f"document {document_id}: raw_sha256 and content_sha256 must be "
            "computed before a record is built"
        )
    checked_url = _check_url(parsed.source_url)
    if isinstance(checked_url, SidecarRejection) or checked_url != parsed.source_url:
        raise ValueError(f"document {document_id}: source_url is not citable")
    sidecar_url = parsed.sidecar.get("source_url")
    if _check_url(sidecar_url) is SidecarRejection.UNCITABLE_SOURCE_URL:
        raise ValueError(f"document {document_id}: the sidecar holds a signed URL")
    if metadata_sha256(parsed.sidecar) != parsed.metadata_sha256:
        raise ValueError(
            f"document {document_id}: metadata_sha256 does not match the sidecar"
        )
    if ref.metadata_sha256 is not None and ref.metadata_sha256 != (
        parsed.metadata_sha256
    ):
        raise ValueError(
            f"document {document_id}: metadata_sha256 on the ref does not "
            "match the parsed sidecar"
        )
    if isinstance(chunk_count, bool) or not isinstance(chunk_count, int):
        raise ValueError(f"chunk_count must be an int, got {chunk_count!r}")
    if chunk_count < 0:
        raise ValueError(f"chunk_count must be >= 0, got {chunk_count}")
    if not isinstance(synced_at, str) or not _ISO_8601_TIMESTAMP.fullmatch(synced_at):
        raise ValueError(
            f"synced_at must be an ISO 8601 timestamp with an offset, got {synced_at!r}"
        )

    return DocumentRecord(
        document_id=document_id,
        source_base_id=ref.source_base_id,
        content_origin=ref.content_origin,
        source_url=parsed.source_url,
        source=parsed.sidecar,
        raw_sha256=ref.raw_sha256,
        content_sha256=ref.content_sha256,
        metadata_sha256=parsed.metadata_sha256,
        chunk_count=chunk_count,
        synced_at=synced_at,
    )


def record_identity(record: DocumentRecord) -> dict[str, str]:
    """The identifying subset repeated on every chunk of a document, so a
    chunk can be traced to its document and cited without the full record.
    Defined once here so no destination picks its own subset."""
    return {
        "document_id": record.document_id,
        "source_base_id": record.source_base_id,
        "content_origin": record.content_origin,
        "source_url": record.source_url,
    }


# --- C6: the ASCII-safe subset ------------------------------------------------


def ascii_identity(record: DocumentRecord) -> dict[str, str]:
    """Ids and hex hashes only: the fields allowed in a transport field that
    takes ASCII alone. No sidecar value is ever in it, because those are
    unbounded and Estonian. source_url is not in it either, because a URL
    can hold raw Unicode.

    Each value is checked against its own shape, which is narrower than
    ASCII: ids are letters, digits, ".", "_" and "-"; hashes are 64
    lowercase hex characters; the origin is "edited" or "cleaned"; the count
    is a non-negative integer. So no space or control character can reach a
    header line. Every value comes from a UUID, a hex digest or an int, so a
    failure means a bug upstream, and raises rather than being stripped.
    """
    if record.content_origin not in _CONTENT_ORIGINS:
        raise ValueError(f"content_origin is not allowed: {record.content_origin!r}")
    if isinstance(record.chunk_count, bool) or not isinstance(record.chunk_count, int):
        raise ValueError(f"chunk_count must be an int, got {record.chunk_count!r}")
    if record.chunk_count < 0:
        raise ValueError(f"chunk_count must be >= 0, got {record.chunk_count}")
    for name, value, shape in (
        ("document_id", record.document_id, _IDENTIFIER),
        ("source_base_id", record.source_base_id, _IDENTIFIER),
        ("raw_sha256", record.raw_sha256, _HEX_SHA256),
        ("content_sha256", record.content_sha256, _HEX_SHA256),
        ("metadata_sha256", record.metadata_sha256, _HEX_SHA256),
    ):
        if not isinstance(value, str) or not shape.fullmatch(value):
            raise ValueError(f"{name} has an unexpected shape: {value!r}")
    return {
        "document_id": record.document_id,
        "source_base_id": record.source_base_id,
        "content_origin": record.content_origin,
        "raw_sha256": record.raw_sha256,
        "content_sha256": record.content_sha256,
        "metadata_sha256": record.metadata_sha256,
        "chunk_count": str(record.chunk_count),
    }
