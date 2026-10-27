"""The llm-module ingest contract (L2), as code.

PROPOSED, NOT AGREED. content-external/docs/llm-module-ingest-contract.md is
the human-readable side of this module and the thing taken to the llm-module
owner; tests/test_llm_module_sink.py validates every JSON example in it
against the models here, so the two cannot drift apart silently.

PYDANTIC HERE, NOT IN core/schemas.py. The core passes plain frozen
dataclasses because nothing left to validate reaches it. This is the other
kind of place: a wire boundary with a service in another repo, on another
release cycle. The request models are `extra="forbid"`, so a field this side
forgot to declare fails a test here rather than in their parser; the response
models ignore extras, so the llm-module can add fields without breaking us.

AGREED_TERMS IS WHAT THE FAR SIDE HAS PROMISED. Every capability the sink
declares that depends on the llm-module's behaviour, rather than on this
code, is read from it. Each field starts at the value that is safe when
nothing is agreed, and changes only when the contract document records the
answer — never to make a deployment work. A capability lie is worse than a
missing capability (D0).
"""

import re
from collections.abc import Collection
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "AGREED_TERMS",
    "COLLECTION_PATTERN",
    "LEGACY_COLLECTIONS",
    "RECORD_REF",
    "SUPPORTED_INGEST_SCHEMA_VERSIONS",
    "AgencyDeleteRequest",
    "ChunkDeleteRequest",
    "ChunkRef",
    "ContractStatus",
    "ContractTerms",
    "DeleteVerdict",
    "DeletedDocumentsResponse",
    "DocumentDeleteRequest",
    "DocumentProbeResponse",
    "IngestChunk",
    "IngestItem",
    "IngestPart",
    "IngestRequest",
    "IngestResponse",
    "ItemVerdict",
    "SweepRequest",
    "agency_delete_path",
    "chunk_delete_path",
    "classify_delete_status",
    "classify_item_status",
    "document_delete_path",
    "document_path",
    "ingest_path",
    "sweep_path",
    "validate_collection",
]

# THE COLLECTION IS IN EVERY PATH (L19). The cutover bootstraps beside the
# live corpus, into a new collection, so the exporter must name the one it
# writes to — the PHYSICAL collection, never the retrieval alias: rollback
# moves the alias back, and an exporter that followed it would start writing
# into the old corpus. It is a path segment rather than a body field so a GET
# probe carries it the same way a POST does.
COLLECTION_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")

# The llm-module's existing corpora (contract review §2): chunked by its own
# chunker, keyed on `document_hash`, and live until the cutover swap. No point
# in them maps to one of ours, so writing into one would mix two id spaces in
# the collection retrieval is answering from. Bootstrap beside, never in place.
LEGACY_COLLECTIONS = frozenset({"contextual_chunks_azure", "contextual_chunks_aws"})


def validate_collection(name: str, protected: Collection[str] = ()) -> str:
    """`name` if the exporter may write to it, else ValueError (L19).

    `protected` is the deployment's own list (LLM_MODULE_PROTECTED_COLLECTIONS)
    on top of LEGACY_COLLECTIONS: the retrieval alias, and any other live
    collection this module cannot know the name of. Both compare without case.
    """
    if not isinstance(name, str) or not COLLECTION_PATTERN.fullmatch(name):
        raise ValueError(
            f"LLM_MODULE_COLLECTION must be 1-64 characters of A-Z, a-z, 0-9, "
            f"'_' or '-', got {name!r}"
        )
    if name.casefold() in LEGACY_COLLECTIONS:
        raise ValueError(
            f"LLM_MODULE_COLLECTION={name} is one of the llm-module's existing "
            "collections, chunked by its own chunker and keyed on document_hash. "
            "L19: bootstrap beside, never in place — name a new collection."
        )
    if name.casefold() in {p.casefold() for p in protected}:
        raise ValueError(
            f"LLM_MODULE_COLLECTION={name} is listed in "
            "LLM_MODULE_PROTECTED_COLLECTIONS — the retrieval alias or another "
            "live collection. The exporter writes to its own physical "
            "collection only: through the alias, a rollback would send its "
            "writes into the old corpus (L19)."
        )
    return name


def _root(collection: str) -> str:
    return f"/ingest/v1/collections/{collection}"


def ingest_path(collection: str) -> str:
    return f"{_root(collection)}/documents"


def document_delete_path(collection: str) -> str:
    return f"{_root(collection)}/documents/delete"


def chunk_delete_path(collection: str, document_id: str) -> str:
    return f"{_root(collection)}/documents/{document_id}/chunks/delete"


def document_path(collection: str, document_id: str) -> str:
    """The read-back probe for one document (L17)."""
    return f"{_root(collection)}/documents/{document_id}"


def agency_delete_path(collection: str, agency_id: str) -> str:
    return f"{_root(collection)}/agencies/{agency_id}/delete"


def sweep_path(collection: str, agency_id: str) -> str:
    return f"{_root(collection)}/agencies/{agency_id}/sweep"


# The versions LlmModuleSink can speak. LLM_MODULE_INGEST_SCHEMA_VERSION must
# name one of them; a later version is added here alongside its models.
SUPPORTED_INGEST_SCHEMA_VERSIONS = frozenset({1})

# The response item that answers for the document record. Chunk items are
# keyed on their chunk_id, which always starts with "c" (core/ids.py), so the
# two can never collide.
RECORD_REF = "record"

NonEmpty = Annotated[str, Field(min_length=1)]
NonNegative = Annotated[int, Field(ge=0)]


ContractStatus = Literal["proposed", "agreed"]


@dataclass(frozen=True)
class ContractTerms:
    """What the llm-module has promised, for one schema version."""

    # Open items are the task breakdown's; the contract document's §13
    # question for each is given alongside.
    #
    # Open item 8 (§13 q3): merge the record onto existing points without
    # content and without a re-embed (L8), ALL-OR-NOTHING per document (L25):
    # one `record` item, applied to every point or to none. True only when
    # both halves are answered yes — a merge that can land on 8 of 12 points
    # leaves a document inconsistent across its own passages, and once in the
    # manifest it is never retried. False until confirmed — the coordinator
    # then re-pushes the document in full and counts it.
    supports_metadata_only_merge: bool
    # Open item 9 (§13 q5): the most documents it will take in parallel (F16).
    # It embeds every chunk it receives, so the ceiling is its owner's to state.
    max_concurrency: int
    # "agreed" only once the contract document records the owner's sign-off
    # (Open items 1, 5 and 7; §13 q1, q2, q4). Until then CONTENT_SINK=
    # llm_module is refused at startup unless a development flag allows it.
    status: ContractStatus = "proposed"
    # Open item 7 (§13 q4): every point carries agency_id as a filterable
    # field. The agency delete (L9, q11) and the sweep (L10, q12) are each a
    # delete by that filter, so neither may be switched on without it — the
    # check below makes that a construction error rather than a review note.
    agency_id_filterable: bool = False
    supports_agency_scoped_delete: bool = False
    supports_stale_sweep: bool = False
    # Open item 10 (§13 q13): GET a document's chunk count back (L17). Without
    # it, reconcile is `unverifiable` (J9).
    supports_read_back: bool = False
    # Whether that probe lists the ordinals held, not just a count.
    read_back_lists_ordinals: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.max_concurrency, bool) or self.max_concurrency < 1:
            raise ValueError(
                f"max_concurrency must be an int >= 1, got {self.max_concurrency!r}"
            )
        if self.status not in ("proposed", "agreed"):
            raise ValueError(
                f"status must be 'proposed' or 'agreed', got {self.status!r}"
            )
        if self.read_back_lists_ordinals and not self.supports_read_back:
            raise ValueError("read_back_lists_ordinals needs supports_read_back")
        for flag in ("supports_agency_scoped_delete", "supports_stale_sweep"):
            if getattr(self, flag) and not self.agency_id_filterable:
                raise ValueError(
                    f"{flag} needs agency_id_filterable: it deletes by the "
                    "agency_id filter (Open item 7, §13 q4)"
                )


AGREED_TERMS = ContractTerms(supports_metadata_only_merge=False, max_concurrency=2)


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class IngestChunk(_Request):
    """One chunk. `chunk_id` is the point id (L4)."""

    chunk_id: NonEmpty
    ordinal: NonNegative
    text: str = Field(repr=False)
    char_start: NonNegative
    char_end: NonNegative

    @model_validator(mode="after")
    def _span_is_ordered(self) -> Self:
        if self.char_end < self.char_start:
            raise ValueError("char_end must be >= char_start")
        return self


class IngestPart(_Request):
    """Where this request sits in a split document (L5)."""

    index: NonNegative
    count: Annotated[int, Field(ge=1)]
    final: bool

    @model_validator(mode="after")
    def _final_is_the_last(self) -> Self:
        if self.index >= self.count:
            raise ValueError("part.index must be < part.count")
        if self.final != (self.index == self.count - 1):
            raise ValueError("part.final must be true on the last part only")
        return self


class IngestRequest(_Request):
    """One document's record plus some or all of its chunks (L3).

    The record fields are core/schemas.py's DocumentRecord, unchanged: the
    sink serialises the record the core built and never reshapes it (C4).
    `chunks=None` is a metadata-only merge (L8); `[]` is a part with no
    chunks, which only a document with no chunks sends.
    """

    ingest_schema_version: Annotated[int, Field(ge=1)]
    agency_id: NonEmpty
    document_id: NonEmpty
    source_base_id: NonEmpty
    content_origin: Literal["edited", "cleaned"]
    source_url: NonEmpty
    raw_sha256: NonEmpty
    content_sha256: NonEmpty
    metadata_sha256: NonEmpty
    chunk_count: NonNegative
    synced_at: NonEmpty
    source: dict[str, Any] = Field(repr=False)
    part: IngestPart
    chunks: list[IngestChunk] | None


class DocumentDeleteRequest(_Request):
    """Delete whole documents (L9): every point of each, then its record."""

    ingest_schema_version: Annotated[int, Field(ge=1)]
    agency_id: NonEmpty
    document_ids: Annotated[list[NonEmpty], Field(min_length=1)]


class ChunkRef(_Request):
    """One chunk to delete, by its point id and its coordinate."""

    chunk_id: NonEmpty
    ordinal: NonNegative


class ChunkDeleteRequest(_Request):
    """Delete a document's tail chunks (L9, C16). The record stays."""

    ingest_schema_version: Annotated[int, Field(ge=1)]
    agency_id: NonEmpty
    document_id: NonEmpty
    chunks: Annotated[list[ChunkRef], Field(min_length=1)]


class AgencyDeleteRequest(_Request):
    """Delete everything held for one agency (L9). Needs Open item 7."""

    ingest_schema_version: Annotated[int, Field(ge=1)]
    agency_id: NonEmpty


class SweepRequest(_Request):
    """Delete the agency's documents last synced before `before` and absent
    from `keep_document_ids` (L10).

    The keep list is what makes the sweep safe: an unchanged document, or one
    whose row is not ready, is never re-sent, so its points keep an old
    `synced_at` while being live. `before` alone would delete them.
    """

    ingest_schema_version: Annotated[int, Field(ge=1)]
    agency_id: NonEmpty
    before: NonEmpty
    keep_document_ids: Annotated[list[NonEmpty], Field(min_length=1)]


class _Response(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class IngestItem(_Response):
    """The outcome for one ref: "record", a chunk_id, or — on a delete — a
    document_id."""

    ref: NonEmpty
    status: Annotated[int, Field(ge=100, le=599)]
    code: str | None = None


class IngestResponse(_Response):
    ingest_schema_version: int
    items: list[IngestItem]


class DeletedDocumentsResponse(_Response):
    """What an agency delete or a sweep removed, by document."""

    ingest_schema_version: int
    deleted_document_ids: list[NonEmpty]


class DocumentProbeResponse(_Response):
    """What the llm-module holds for one document (L17)."""

    ingest_schema_version: int
    document_id: NonEmpty
    record_present: bool
    chunk_count: NonNegative
    chunk_ordinals: list[NonNegative] | None = None


ItemVerdict = Literal["applied", "retryable", "permanent"]
DeleteVerdict = Literal["deleted", "missing", "retryable", "failed"]

# Key Design #6's per-item rule: a conflict, a validation race and an
# overloaded embedder can clear on a resend; anything else will not.
_RETRYABLE_ITEM_STATUSES = frozenset({409, 422, 503})

# On a delete, "not there" is success (D3): already gone is what was asked.
_MISSING_ITEM_STATUSES = frozenset({404, 410})


def classify_item_status(status: int) -> ItemVerdict:
    """What one item's status means for the document it belongs to (L6)."""
    if 200 <= status < 300:
        return "applied"
    if status in _RETRYABLE_ITEM_STATUSES:
        return "retryable"
    return "permanent"


def classify_delete_status(status: int) -> DeleteVerdict:
    """What one delete item's status means (L9)."""
    if 200 <= status < 300:
        return "deleted"
    if status in _MISSING_ITEM_STATUSES:
        return "missing"
    if status in _RETRYABLE_ITEM_STATUSES:
        return "retryable"
    return "failed"
