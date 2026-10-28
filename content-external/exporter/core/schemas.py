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
    url: str | None
    page_title: str | None
    subsector: str | None
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

    # Set by the document source when a read-time guard rejects the document
    # (oversize, a SidecarRejection value). diff.classify() files it under
    # `skipped` with this reason, and holds it if it was published before.
    # None for every document nothing rejected. A guard that can only run
    # after chunking, such as too many chunks, cannot use this field: it
    # goes through DiffResult.demote_to_skipped() instead.
    skip_reason: str | None = None


@dataclass(frozen=True)
class AgencyWatermark:
    """max_updated_at is None when the agency has no rows."""

    max_updated_at: str | None
    live_count: int


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
    # Out of the repr (F13): a Chunk in an f-string, an assertion message or
    # a traceback would otherwise carry Estonian government content.
    text: str = field(repr=False)
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
    # Out of the repr (F13): it holds the title and other document text.
    source: Mapping[str, Any] = field(repr=False)
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
    # REQUIRE_METADATA_SIDECAR is off. Out of the repr, as DocumentRecord.source.
    sidecar: Mapping[str, Any] = field(repr=False)
    metadata_sha256: str
    source_url: str  # the resolved citation
    source_url_origin: str  # "sidecar" | "row"
    # Both URLs valid and different; the sidecar's was kept. Counted, never
    # logged per document.
    source_url_mismatch: bool

    def __post_init__(self) -> None:
        _freeze_fields(self, "sidecar")


@dataclass(frozen=True)
class ReadDocument:
    """The read contract checked by the publish coordinator:

    1. text is NFC-normalised and is None exactly when ref.skip_reason is set.
    2. ref equals the input ref except for raw_sha256, content_sha256,
       metadata_sha256 and skip_reason.
    3. ref.metadata_sha256 equals the hash of the parsed sidecar.
    4. A failed read (S3/network error) raises StoreError, never a skip.
       Only read-time guards (oversize, sidecar rejection) set skip_reason.
    """

    ref: DocumentRef
    text: str | None
    sidecar: ParsedSidecar | None


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
    file_size: int | None  # source_file.file_size, audit only; may be absent
    chunk_count: int
    # The geometry this document's live chunks were cut with. Per entry, not
    # only per manifest: a document held or failed through a fingerprint
    # change keeps its old chunks, and must still be re-chunked once it is
    # read again.
    chunker_fingerprint: str
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
    # Delete intents journalled before the destination was asked (F7), and
    # the deleted items it did not confirm, which stay with the journal.
    deletions_recorded: int = 0
    deletions_unresolved: int = 0

    # Documents the destination did not confirm. They keep their previous
    # manifest entry and are retried next run, so a run with failures can still
    # be `success`; this count is what tells the two apart.
    publish_failed_count: int = 0
    # F18. Every upsert's outcome, counted by status (published,
    # failed_permanent, failed_retryable, deferred), and each document that
    # did not publish with why: its status, or rechunk_count_mismatch for a
    # document held because its re-chunk disagreed with the published count.
    # A destination answering "207, nine items failed" is not success, and a
    # single count would make the two indistinguishable. Only published
    # documents enter the manifest, so this and the manifest agree.
    upsert_outcomes: Mapping[str, int] = field(default_factory=dict)
    publish_failures: Mapping[str, str] = field(default_factory=dict)
    # metadata_changed documents re-pushed in full because the destination has
    # no metadata-only merge (L8): a cheap path that became an expensive one,
    # reported rather than hidden.
    metadata_full_repush_count: int = 0

    # Citation cross-checks (C2): documents cited from source_file.url
    # because the sidecar's source_url was unusable, and documents whose two
    # valid URLs disagreed.
    source_url_fallback_count: int = 0
    source_url_mismatch_count: int = 0

    # Wall-clock time from the run's start to its report (F11, G9): a run
    # that routinely approaches the cron interval is the signal to lengthen it.
    duration_seconds: float = 0.0
    # First-run manifest checkpoints committed before the final commit (F15).
    checkpoints_committed: int = 0
    # The exception class name behind a `failed` outcome, and nothing more:
    # the report reaches /health, which is unauthenticated, and a message
    # could name more than an operator needs there. The sanitised message is
    # in the log line.
    error_type: str | None = None
    # How the run was asked for (F10): --force, and --limit's cap. A limited
    # run withholds the corpus watermark, so these explain a report whose
    # skipped count is larger than CKB's state alone accounts for.
    forced: bool = False
    limit: int | None = None
    # F20. True only when the run classified every row: not --limit, not
    # aborted, not ended early. Everything concluded from completeness reads
    # this one flag — the watermark advance now, every sweep later — and never
    # re-derives it. False on busy and failed, which observed nothing.
    run_observed_full_corpus: bool = False
    # Documents this run should have brought up to date and did not — not
    # published, a tail not confirmed deleted, or skipped by a guard whose
    # verdict depends on configuration. Each keeps its old state and its old
    # synced_at. Seeing the whole corpus is not enough for a sweep: these
    # would look stale to it, so any sweep also requires this to be 0.
    documents_held_back: int = 0
    # E9. Documents in the dead-letter record after this run: stuck on a
    # fault of their own (a read-time guard or MAX_CHUNKS_PER_DOCUMENT).
    # Which ones, why and since when is in the record itself
    # (services/dead_letter.py). Busy, failed and unchanged runs do not update
    # it and report its size as it stands.
    dead_lettered_count: int = 0
    # F21, on a first run only: how the "is the destination already
    # populated?" check came out — "empty", "resumed" (our own bootstrap that
    # never committed), "overridden" (--accept-existing-content) or
    # "unverifiable" (the sink can neither list nor read back), or
    # "populated" on the run it refused. None on an incremental run, which
    # never probes.
    first_run_probe: str | None = None
    # How the run was asked for: --accept-existing-content (F21).
    accepted_existing_content: bool = False
    # L10, after the commit: "disabled" (STALE_SWEEP_ENABLED is off),
    # "not_run" (it is on, but the run ended `unchanged`, `failed` or `busy`
    # and so never reached the sweep step),
    # "refused" (the run cannot vouch for the whole corpus, F20),
    # "not_supported" (the destination has no such sweep), "failed" (the
    # commit stands; the next full run sweeps again) or "done", with the
    # documents it removed. Never changes the outcome.
    stale_sweep: str = "disabled"
    stale_swept_count: int = 0
    # L18: None when no re-chunk was pending. Otherwise "proceeded" (the
    # destination does not need an acknowledgement), "accepted"
    # (--accept-rechunk) or "refused" on the run it failed.
    rechunk: str | None = None
    # How the run was asked for: --accept-rechunk (L18).
    accepted_rechunk: bool = False

    def __post_init__(self) -> None:
        _freeze_fields(self, "skipped_reasons", "upsert_outcomes", "publish_failures")
