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
process, not only by this one.

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

from dataclasses import dataclass, field


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

    content_key: str | None  # edited_data_url, falling back to cleaned_data_url
    metadata_key: str | None  # edited_metadata_url, falling back to cleaned_metadata_url
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
    source: dict  # the sidecar, verbatim, nested so upstream keys cannot collide with ours
    raw_sha256: str
    content_sha256: str
    metadata_sha256: str
    chunk_count: int
    synced_at: str  # ISO 8601


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
    documents: dict[str, ManifestDocumentEntry] = field(default_factory=dict)


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

    skipped_reasons: dict[str, str] = field(default_factory=dict)  # document_id -> reason
    deletions_recorded: int = 0
