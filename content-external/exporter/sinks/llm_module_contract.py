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

from dataclasses import dataclass
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "AGREED_TERMS",
    "INGEST_PATH",
    "RECORD_REF",
    "SUPPORTED_INGEST_SCHEMA_VERSIONS",
    "ContractTerms",
    "IngestChunk",
    "IngestItem",
    "IngestPart",
    "IngestRequest",
    "IngestResponse",
    "ItemVerdict",
    "classify_item_status",
]

INGEST_PATH = "/ingest/v1/documents"

# The versions LlmModuleSink can speak. LLM_MODULE_INGEST_SCHEMA_VERSION must
# name one of them; a later version is added here alongside its models.
SUPPORTED_INGEST_SCHEMA_VERSIONS = frozenset({1})

# The response item that answers for the document record. Chunk items are
# keyed on their chunk_id, which always starts with "c" (core/ids.py), so the
# two can never collide.
RECORD_REF = "record"

NonEmpty = Annotated[str, Field(min_length=1)]
NonNegative = Annotated[int, Field(ge=0)]


@dataclass(frozen=True)
class ContractTerms:
    """What the llm-module has promised, for one schema version."""

    # Open item 8: merge the record onto existing points without content and
    # without a re-embed (L8). False until confirmed — the coordinator then
    # re-pushes the document in full and counts it.
    supports_metadata_only_merge: bool
    # Open item 9: the most documents it will take in parallel (F16). It
    # embeds every chunk it receives, so the ceiling is its owner's to state.
    max_concurrency: int

    def __post_init__(self) -> None:
        if isinstance(self.max_concurrency, bool) or self.max_concurrency < 1:
            raise ValueError(
                f"max_concurrency must be an int >= 1, got {self.max_concurrency!r}"
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


class _Response(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class IngestItem(_Response):
    """The outcome for one ref: "record", or a chunk_id."""

    ref: NonEmpty
    status: Annotated[int, Field(ge=100, le=599)]
    code: str | None = None


class IngestResponse(_Response):
    ingest_schema_version: int
    items: list[IngestItem]


ItemVerdict = Literal["applied", "retryable", "permanent"]

# Key Design #6's per-item rule: a conflict, a validation race and an
# overloaded embedder can clear on a resend; anything else will not.
_RETRYABLE_ITEM_STATUSES = frozenset({409, 422, 503})


def classify_item_status(status: int) -> ItemVerdict:
    """What one item's status means for the document it belongs to (L6)."""
    if 200 <= status < 300:
        return "applied"
    if status in _RETRYABLE_ITEM_STATUSES:
        return "retryable"
    return "permanent"
