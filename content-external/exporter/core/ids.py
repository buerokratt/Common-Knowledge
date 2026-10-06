"""Chunk id generation and blob key builders.

A chunk id is a function of coordinates only — agency, document and
ordinal — never of the chunk's own text. If it hashed the text, editing
one paragraph would shift every downstream chunk's content and re-key the
whole document: a small correction would look like a full delete-and-
reinsert. Keyed on coordinates, the same edit overwrites the *same* ids,
so the update path is idempotent by construction. Whether the text itself
changed is still detected separately, via content_sha256 in the manifest —
that is the right place for it, not here.

The key builders fill in the blob key templates from constants.py with
real values (agency id, document id, ordinal, run id) and return a plain
string. They do not write anything and do not know what a "store" or a
"sink" is — only the object-store sink actually uses the strings they
return, to name where each file is saved; the llm-module sink is not
file-based and never calls these functions at all.
"""

import hashlib

from exporter.core.constants import (
    CHUNKER_VERSION,
    DOCUMENT_CHUNK_BLOB_KEY_TEMPLATE,
    DOCUMENT_METADATA_BLOB_KEY_TEMPLATE,
    ID_NAMESPACE,
    ID_VERSION,
    MANIFEST_BLOB_KEY_TEMPLATE,
    MAX_CHUNK_ORDINAL,
    NORMALISER_VERSION,
    PENDING_DELETIONS_BLOB_KEY_TEMPLATE,
    ChunkProfile,
)

# Hex digest length, in bytes -> 32 hex characters. "128" in blake2b_128
# refers to this digest size, not to blake2b's own (512-bit) internal state.
_CHUNK_ID_DIGEST_SIZE = 16

# Leads every id, so a chunk id is never mistaken for a plain hex number
# and is always safe as a key/identifier in stores that dislike a
# leading digit.
_CHUNK_ID_PREFIX = "c"


# There is deliberately no make_document_id() in this module. document_id
# is always source_file.base_id, taken directly from CKB with no hashing,
# derivation or normalisation of any kind. An earlier draft derived it from
# a file path instead, which reintroduced three bugs at once: two
# byte-identical documents could collapse into one entry, a document whose
# storage path moved would read as a deleted-and-new pair and orphan its
# chunks, and the derivation itself needed defending for stability across
# an archive whose layout could shift. Reusing CKB's own stable key removes
# all three rather than fixing any of them — do not add a helper that
# re-derives document_id from anything.


def make_chunk_id(agency_id: str, document_id: str, ordinal: int) -> str:
    """A chunk's id: "c" + blake2b_128 hex of its coordinates.

    Coordinates only — agency_id, document_id, ordinal — plus ID_NAMESPACE
    and ID_VERSION so this service's ids cannot collide with an unrelated
    system's, and so a deliberate ID_VERSION bump re-keys every chunk this
    service has ever produced.
    """
    payload = "\x1f".join(
        (ID_NAMESPACE, str(ID_VERSION), agency_id, document_id, str(ordinal))
    ).encode("utf-8")
    digest = hashlib.blake2b(payload, digest_size=_CHUNK_ID_DIGEST_SIZE).hexdigest()
    return _CHUNK_ID_PREFIX + digest


def chunker_fingerprint(profile: ChunkProfile) -> str:
    """A single hash standing in for "how this run would chunk and id a
    document", covering CHUNKER_VERSION, NORMALISER_VERSION, ID_NAMESPACE,
    ID_VERSION and the four resolved size params of the active profile —
    and nothing else.

    Stored in the manifest. If it differs from the value the manifest was
    last committed with, the diff treats every document as content_changed,
    which is what makes a chunking-logic or profile change re-chunk the
    whole corpus cleanly instead of leaving some documents on old geometry
    and some on new.

    ID_NAMESPACE and ID_VERSION are here for the same reason: changing
    either re-keys every chunk id,
    and without it only documents whose content happened to change would
    be re-published, leaving one corpus on two id schemes — and old-scheme
    ids that no later run can compute, so can never delete.

    Must NOT include sink or store identity. Switching destination
    does not change how a document would be chunked, so it must not force
    a re-chunk — and on the llm-module sink, a re-chunk means a full
    corpus re-embed on someone else's service. Destination identity is
    guarded separately, by the manifest's own sink_id field.
    """
    payload = "\x1f".join(
        (
            str(CHUNKER_VERSION),
            str(NORMALISER_VERSION),
            ID_NAMESPACE,
            str(ID_VERSION),
            str(profile.target),
            str(profile.overlap),
            str(profile.min),
            str(profile.max),
        )
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# The key builders validate what they are given rather than trust it: a key
# is a path, and "../x", "" or "a/b" as an id would write somewhere the
# orphan sweep and the manifest never look. Real ids are UUIDs, so these
# only ever fire on a bug upstream — which is when they are wanted.


def _segment(name: str, value: str) -> str:
    """One path segment: non-empty, no "/", and not "." or ".."."""
    if not isinstance(value, str) or not value or "/" in value:
        raise ValueError(
            f"{name} must be a non-empty string without '/', got {value!r}"
        )
    if value in (".", ".."):
        raise ValueError(f"{name} must not be {value!r}")
    return value


def validate_prefix(prefix: str) -> str:
    """The deployment prefix: may hold "/" between segments, but no empty,
    "." or ".." segment and no leading or trailing "/"."""
    if not isinstance(prefix, str) or not prefix:
        raise ValueError(f"prefix must be a non-empty string, got {prefix!r}")
    for part in prefix.split("/"):
        _segment("prefix segment", part)
    return prefix


def _ordinal(ordinal: int) -> int:
    if isinstance(ordinal, bool) or not isinstance(ordinal, int):
        raise ValueError(f"ordinal must be an int, got {ordinal!r}")
    if not 0 <= ordinal <= MAX_CHUNK_ORDINAL:
        raise ValueError(f"ordinal must be in [0, {MAX_CHUNK_ORDINAL}], got {ordinal}")
    return ordinal


def manifest_key(prefix: str, agency_id: str) -> str:
    """The one manifest.json for this (prefix, agency)."""
    return MANIFEST_BLOB_KEY_TEMPLATE.format(
        prefix=validate_prefix(prefix), agency_id=_segment("agency_id", agency_id)
    )


def document_metadata_key(prefix: str, agency_id: str, document_id: str) -> str:
    """metadata.json sits beside chunks/, not inside it, so the metadata-only
    publish path and the chunk-publish path own disjoint key spaces and
    either can be replayed alone."""
    return DOCUMENT_METADATA_BLOB_KEY_TEMPLATE.format(
        prefix=validate_prefix(prefix),
        agency_id=_segment("agency_id", agency_id),
        document_id=_segment("document_id", document_id),
    )


def document_chunk_key(
    prefix: str, agency_id: str, document_id: str, ordinal: int
) -> str:
    """Keyed on ordinal, not chunk_id: ordinal -> id is a pure function
    while id -> ordinal is not, which is what lets the orphan sweep list a
    prefix and drop every ordinal >= the current chunk_count with no
    manifest needed."""
    return DOCUMENT_CHUNK_BLOB_KEY_TEMPLATE.format(
        prefix=validate_prefix(prefix),
        agency_id=_segment("agency_id", agency_id),
        document_id=_segment("document_id", document_id),
        ordinal=_ordinal(ordinal),
    )


def pending_deletions_key(prefix: str, run_id: str) -> str:
    """The deferred-deletion journal for one run, written before a delete
    is attempted so an incident is recorded even if the destination that
    should have received it is what is broken."""
    return PENDING_DELETIONS_BLOB_KEY_TEMPLATE.format(
        prefix=validate_prefix(prefix), run_id=_segment("run_id", run_id)
    )
