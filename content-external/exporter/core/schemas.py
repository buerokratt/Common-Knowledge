"""Shared data shapes passed between the pure core, the diff, the publish
coordinator and the sinks.

Plain frozen dataclasses, not pydantic: by the time data reaches this layer
it has already been read from CKB or from a committed manifest, so there is
nothing left to validate — pydantic stays at the API boundary
(exporter/api/config.py, exporter/api/models.py) where untrusted input
actually arrives. Frozen so nothing downstream can mutate a shape another
function is still holding a reference to.

Every one of these is serialised with json.dump, never pickled — the
manifest and the run report are meant to be read by a human or another
process, not only by this one. json.dump cannot take a dataclass, so
to_json_dict() turns one into plain dicts and lists first.

frozen=True only stops attribute rebinding; a dict field would still be
mutable in place (`manifest.documents["x"] = ...`). So every mapping field
is frozen on construction into a read-only view, nested values included.

TextSpan and Chunk are the two shapes the design's own documents never spell
out field-by-field (they are named once in a file listing and nowhere
else). Their fields here are built from what the documents DO state
directly elsewhere: chunks are addressed by ordinal and stored as
chunks/{ordinal:05d}.json; chunk_id is a pure function of
(agency_id, document_id, ordinal) and never of the chunk's own text; and
"chunks minus overlap reconstruct the normalised text exactly" requires
each chunk to carry both its text and its position in the source. Flagged
here rather than presented as a quoted spec.
"""

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any


def _freeze(value: object) -> object:
    """A read-only copy: mappings become MappingProxyType over a fresh dict,
    lists and tuples become tuples, recursively. Copying first means the
    caller's own dict cannot change the frozen value behind its back."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _freeze_fields(instance: object, *names: str) -> None:
    for name in names:
        object.__setattr__(instance, name, _freeze(getattr(instance, name)))


def to_json_dict(value: object) -> object:
    """`value` as plain dicts, lists and scalars, ready for json.dump: the
    inverse of the freezing above, applied through nested dataclasses."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: to_json_dict(getattr(value, f.name))
            for f in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {key: to_json_dict(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json_dict(item) for item in value]
    return value


@dataclass(frozen=True)
class DocumentRef:
    """One row from list_agency_export_documents.sql, plus the three hashes
    once a document has actually been read. Carries hashes and keys, never
    the document's text — so a list of these does not hold a corpus in
    memory, only what is needed to classify it.
    """

    document_id: str  # source_file.base_id — never derived, see Finding 8
    source_base_id: str
    agency_id: str
    url: str
    page_title: str
    subsector: str
    status: str
    is_excluded: bool
    is_deleted: bool
    updated_at: str  # ISO 8601, as returned by Resql

    # edited_data_url / edited_metadata_url, falling back to the cleaned_* ones
    content_key: str | None
    metadata_key: str | None
    content_origin: str | None  # "edited" | "cleaned" | None when neither exists
    file_size: int | None

    # None until the document has actually been read from S3 and hashed —
    # a DocumentRef built straight from the enumeration query has none of
    # these yet.
    raw_sha256: str | None = None
    content_sha256: str | None = None
    metadata_sha256: str | None = None


@dataclass(frozen=True)
class TextSpan:
    """A half-open [start, end) character range into a document's
    normalised text. Not a value class an operator ever sees — it is the
    chunker's own bookkeeping for where a chunk came from, kept separate
    from Chunk so the overlap-reconstruction check can compare spans
    without re-deriving them from chunk text.
    """

    start: int
    end: int


@dataclass(frozen=True)
class Chunk:
    """One retrieval-sized passage of a document's text."""

    chunk_id: str  # make_chunk_id(agency_id, document_id, ordinal)
    document_id: str
    ordinal: int  # position within the document — chunks/{ordinal:05d}.json
    text: str
    span: TextSpan  # where in the normalised document this text came from


@dataclass(frozen=True)
class DocumentRecord:
    """The canonical document record. Built once, sink-agnostic: the
    object-store sink serialises this as metadata.json; the llm-module sink
    carries the same fields on the document envelope. Neither sink
    constructs its own shape.
    """

    document_id: str
    source_base_id: str
    content_origin: str  # "edited" | "cleaned"
    # The resolved citation: the sidecar's source_url, or source_file.url
    # when the sidecar's is missing or invalid. Top-level rather than written
    # back into `source`, which stays verbatim.
    source_url: str
    # The sidecar, verbatim, nested so upstream keys cannot collide with ours.
    source: Mapping[str, Any]
    raw_sha256: str
    content_sha256: str
    metadata_sha256: str
    chunk_count: int
    synced_at: str  # ISO 8601

    def __post_init__(self) -> None:
        _freeze_fields(self, "source")


@dataclass(frozen=True)
class ParsedSidecar:
    """What metadata.parse_sidecar() hands back for a usable sidecar: the
    sidecar itself, its canonical hash and the resolved citation. Carries no
    content text."""

    # The parsed JSON object, verbatim. {} when no sidecar exists and
    # REQUIRE_METADATA_SIDECAR is off.
    sidecar: Mapping[str, Any]
    metadata_sha256: str
    source_url: str  # the resolved citation
    source_url_origin: str  # "sidecar" | "row"
    # Both URLs valid and different; the sidecar's was kept. Counted, never
    # logged per document.
    source_url_mismatch: bool

    def __post_init__(self) -> None:
        _freeze_fields(self, "sidecar")


@dataclass(frozen=True)
class ManifestDocumentEntry:
    """One entry under Manifest.documents — the diff module's entire
    memory of a single document's previously-published state."""

    source_base_id: str
    content_origin: str
    source_updated_at: str
    source_status: str
    raw_sha256: str
    content_sha256: str
    metadata_sha256: str
    file_size: int
    chunk_count: int
    state: str  # e.g. "published"
    processed_at: str


@dataclass(frozen=True)
class Manifest:
    """One manifest per (agency, sink) — the diff module's entire previous
    state. Committed with a single atomic PUT to the manifest store, never
    the destination. sink_id is checked on load: a mismatch against the
    configured sink is `failed`, never treated as a first run.
    """

    schema_version: int
    manifest_schema_version: int
    agency_id: str
    sink_id: str
    corpus_watermark: str | None  # null until the first full run completes
    document_count: int
    chunker_fingerprint: str
    committed_at: str
    documents: Mapping[str, ManifestDocumentEntry] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _freeze_fields(self, "documents")


@dataclass(frozen=True)
class StoreObject:
    """What a store operation confirms happened. Returned by
    ExternalStoreClient.put_bytes() and yielded by list_keys()."""

    key: str
    size_bytes: int
    etag: str | None = None


@dataclass(frozen=True)
class RunReport:
    """One run's outcome, for operators. No ack semantics — the next tick
    is the retry, so this exists to be read, not acted on by a caller."""

    run_id: str
    agency_id: str
    sink_id: str
    outcome: str  # "success" | "unchanged" | "busy" | "failed"
    started_at: str
    finished_at: str | None

    new_count: int = 0
    content_changed_count: int = 0
    metadata_changed_count: int = 0
    unchanged_count: int = 0
    deleted_count: int = 0
    skipped_count: int = 0

    # document_id -> reason
    skipped_reasons: Mapping[str, str] = field(default_factory=dict)
    deletions_recorded: int = 0

    # Citation cross-checks (C2): documents cited from source_file.url
    # because the sidecar's source_url was unusable, and documents whose two
    # valid URLs disagreed.
    source_url_fallback_count: int = 0
    source_url_mismatch_count: int = 0

    def __post_init__(self) -> None:
        _freeze_fields(self, "skipped_reasons")
