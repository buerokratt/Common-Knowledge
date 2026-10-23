"""The ContentSink — the publish coordinator's only destination interface (D0).

WHY TWO LAYERS, NOT ONE WIDER STORE ABC. Forcing the llm-module behind
`put_bytes(key, payload)` would be a lie in three places: it cannot list keys,
its delete unit is a document rather than a key, and "put the metadata blob,
then put ten chunk blobs" is eleven requests where one document should be one.
A sink that has to pretend to be a key-value store is a sink whose failure
modes the coordinator cannot see. So the sink speaks destination-neutral verbs
— publish this document, delete these documents — and the object-store sink
(D14) implements them over an `ExternalStoreClient`, which stays exactly as
Key Design #1 specifies, one level down.

The coordinator holds a ContentSink and a manifest store, never an
ExternalStoreClient. If it ever needs a third thing, that is a design
conversation, not an import.

BOUND TO ONE AGENCY. A sink is constructed for one agency's run. Neither
`DocumentRecord` nor the D0 verbs carry an agency id, yet both sinks need one
— the object-store sink for its keys, the llm-module sink for its payload — so
it is fixed at construction, and CKB's one-agency-per-deployment rule (A17)
makes one sink per run the natural unit. `delete_agency` and `sweep_stale`
still take the agency explicitly, as D0 specifies, and refuse a mismatch: a
purge aimed at the wrong tenant fails loudly rather than resolving to "this
one".

PUBLIC VERBS ARE CONCRETE, SINKS IMPLEMENT THE HOOKS. Each public method
enforces the rules every sink must share — the capability check, the bound
agency, the shape of the chunk list — calls one underscore hook, and checks
that the outcome answers for what was asked.
"Every undeclared capability raises NotSupported" is therefore a property of
this class, not a discipline each sink has to remember.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from exporter.core.redaction import sanitize_sensitive_text
from exporter.core.schemas import Chunk, DocumentRecord
from exporter.stores.base import (
    DeleteOutcome,
    NotSupported,
    StoreError,
    account_for_requested,
)

__all__ = [
    "ContentSink",
    "DeleteOutcome",
    "DocumentProbe",
    "NotSupported",
    "SinkCapabilities",
    "StoreError",
    "UpsertOutcome",
    "UpsertStatus",
]


@dataclass(frozen=True)
class SinkCapabilities:
    """What a sink can do, declared honestly — the parity matrix in code.

    A capability lie is worse than a missing capability: the degradation
    paths (manifest-trusted reconcile, the `synced_at` sweep, a reported full
    re-push) are the only thing standing between a limited destination and a
    sweep that deletes live content.
    """

    can_list: bool
    can_read_back: bool
    supports_metadata_only_merge: bool
    supports_batch_delete: bool
    # True when the transport itself makes a document all-or-nothing. False
    # does NOT mean the F3 guarantee is absent — the object-store sink meets
    # it by write order instead.
    atomic_per_document: bool
    supports_agency_scoped_delete: bool
    # F16. The most documents this destination should take in parallel. The
    # sink states it because the bottleneck is the destination's: an object
    # store absorbs 8-16 writers, while the llm-module embeds every chunk it
    # receives, so its ceiling is its owner's to state (Open item 9). The
    # coordinator runs min(EXPORT_CONCURRENCY, this).
    max_concurrency: int

    def __post_init__(self) -> None:
        if isinstance(self.max_concurrency, bool) or self.max_concurrency < 1:
            raise ValueError(
                f"max_concurrency must be an int >= 1, got {self.max_concurrency!r}"
            )


class UpsertStatus(StrEnum):
    """Per-document sink outcome (F18). Only PUBLISHED enters the manifest."""

    PUBLISHED = "published"
    FAILED_PERMANENT = "failed_permanent"
    FAILED_RETRYABLE = "failed_retryable"
    DEFERRED = "deferred"


@dataclass(frozen=True)
class UpsertOutcome:
    """What one `upsert_document` call achieved.

    `detail` is a short code or reason for an operator — sanitised, and never
    document text or a request/response body (L14).
    """

    document_id: str
    status: UpsertStatus
    chunks_written: int
    metadata_only: bool
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.chunks_written < 0:
            raise ValueError(f"chunks_written must be >= 0, got {self.chunks_written}")
        if self.metadata_only and self.chunks_written:
            raise ValueError("a metadata-only upsert writes no chunks")
        if self.detail is not None:
            object.__setattr__(self, "detail", sanitize_sensitive_text(self.detail))

    @property
    def published(self) -> bool:
        return self.status is UpsertStatus.PUBLISHED


@dataclass(frozen=True)
class DocumentProbe:
    """What the destination holds for one document — reconcile's read-back.

    `chunk_ordinals` is None when a sink can only count; when present it is
    what lets the conformance suite (D15) assert that a tail delete left every
    surviving chunk in place.
    """

    document_id: str
    record_present: bool
    chunk_count: int
    chunk_ordinals: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        if self.chunk_count < 0:
            raise ValueError(f"chunk_count must be >= 0, got {self.chunk_count}")
        if self.chunk_ordinals is not None:
            ordinals = tuple(sorted(self.chunk_ordinals))
            object.__setattr__(self, "chunk_ordinals", ordinals)
            if len(ordinals) != self.chunk_count:
                raise ValueError(
                    f"chunk_count={self.chunk_count} disagrees with "
                    f"{len(ordinals)} chunk ordinals"
                )


class ContentSink(ABC):
    """Publish and delete documents at one destination, for one agency.

    THE CONTRACT every implementation meets, and that the sink conformance
    suite (D15) asserts rather than assumes:

    - F3: no chunk is retrievable while its document's record is missing.
      How is the sink's business — write order for the object-store sink,
      one request per document for the llm-module sink.
    - F4: on delete, chunks go before the record, so nothing ever references
      content that is already gone.
    - Upserts are idempotent: chunk ids are deterministic coordinates, so a
      replayed upsert overwrites the same chunks rather than adding new ones.
    - "Already gone" is success: deleting an absent document reports
      `missing`, never `failed`.
    - Undeclared capabilities raise NotSupported, never silently no-op.
    - Every outcome answers for exactly what was asked: a delete reports each
      requested item once, and an upsert reports the document it was given
      and the chunks it actually wrote. The base class checks both.
    """

    def __init__(self, *, agency_id: str, sink_id: str) -> None:
        if not isinstance(agency_id, str) or not agency_id:
            raise ValueError(f"agency_id must be a non-empty string, got {agency_id!r}")
        if not isinstance(sink_id, str) or not sink_id:
            raise ValueError(f"sink_id must be a non-empty string, got {sink_id!r}")
        self._agency_id = agency_id
        self._sink_id = sink_id

    @property
    def agency_id(self) -> str:
        """The one agency this sink publishes for."""
        return self._agency_id

    @property
    def sink_id(self) -> str:
        """The destination name the manifest records (F17). Supplied by the
        factory, which is the only place that knows CONTENT_SINK, so no sink
        class hard-codes its own name."""
        return self._sink_id

    @property
    @abstractmethod
    def capabilities(self) -> SinkCapabilities:
        """What this sink can do — declared, never inferred at runtime."""

    # -- public verbs (D0) -------------------------------------------------

    def upsert_document(
        self, record: DocumentRecord, chunks: Sequence[Chunk] | None
    ) -> UpsertOutcome:
        """Publish one document: its record and, unless `chunks` is None, its
        chunks.

        The record and chunks arrive together so one sink can write two
        artifacts in order and the other can send one request, without the
        coordinator knowing which.

        `chunks=None` is a metadata-only merge (the `metadata_changed`
        bucket). A sink that cannot do one raises NotSupported, so the
        coordinator falls back to a full re-push explicitly and reports the
        cost, rather than a "cheap" path quietly becoming an expensive one
        (L8).

        When chunks are given they must be exactly this document's chunks,
        ordinals 0..chunk_count-1. Anything else is a coordinator bug, and
        writing it would leave a destination that disagrees with the record
        beside it.
        """
        if chunks is None:
            self._require(
                self.capabilities.supports_metadata_only_merge,
                "supports_metadata_only_merge",
            )
            return self._checked_upsert(
                record, None, self._upsert_document(record, None)
            )

        chunks = tuple(chunks)
        if len(chunks) != record.chunk_count:
            raise ValueError(
                f"document {record.document_id}: record says chunk_count="
                f"{record.chunk_count} but {len(chunks)} chunks were given"
            )
        if any(chunk.document_id != record.document_id for chunk in chunks):
            raise ValueError(
                f"document {record.document_id}: a chunk belongs to another document"
            )
        if [chunk.ordinal for chunk in chunks] != list(range(len(chunks))):
            raise ValueError(
                f"document {record.document_id}: chunk ordinals must be "
                "0..chunk_count-1 in order"
            )
        return self._checked_upsert(
            record, chunks, self._upsert_document(record, chunks)
        )

    def delete_documents(self, document_ids: Sequence[str]) -> DeleteOutcome[str]:
        """Delete whole documents — chunks first, then the record (F4).
        Duplicates are reported once; empty input does nothing. A document
        the sink forgot to report is recorded as failed."""
        requested = tuple(dict.fromkeys(document_ids))
        if not requested:
            return DeleteOutcome()
        return account_for_requested(
            requested, self._delete_documents(requested), reporter=self._reporter
        )

    def delete_chunks(
        self, document_id: str, ordinals: Sequence[int]
    ) -> DeleteOutcome[int]:
        """Delete one document's tail chunks, by coordinate (C16) — never by
        key, which would put a destination detail in the diff."""
        requested = tuple(sorted(set(ordinals)))
        if not requested:
            return DeleteOutcome()
        if requested[0] < 0:
            raise ValueError(f"ordinals must be >= 0, got {requested[0]}")
        return account_for_requested(
            requested,
            self._delete_chunks(document_id, requested),
            reporter=self._reporter,
        )

    def delete_agency(self, agency_id: str) -> DeleteOutcome[str]:
        """Delete everything this sink holds for the agency."""
        self._require_bound_agency(agency_id)
        self._require(
            self.capabilities.supports_agency_scoped_delete,
            "supports_agency_scoped_delete",
        )
        return self._delete_agency()

    def sweep_stale(self, agency_id: str, before: datetime) -> DeleteOutcome[str]:
        """Delete documents last synced before `before`.

        ONLY VALID AFTER A RUN THAT OBSERVED THE FULL CORPUS. After a
        `--limit` run, or one that ended early, everything it did not touch
        is "stale" and this would delete live content. The guard belongs to
        the caller: services/corpus_guard.py's require_observed_full_corpus
        (F20), which reads the run's `run_observed_full_corpus` and never
        re-derives it. This method cannot know how the run went, so it must
        never be called speculatively.

        No D0 capability gates this verb: the llm-module sink sweeps by
        `synced_at` without being able to list (L10), so neither `can_list`
        nor any other field says whether a sink can sweep. A sink that cannot
        raises NotSupported from its hook, and the sink conformance suite
        (D15) asserts that it does rather than no-op'ing.

        A cutoff in the future is refused: `before` is the run's start, and
        a later one would make every document stale.
        """
        self._require_bound_agency(agency_id)
        if before.tzinfo is None or before.utcoffset() is None:
            raise ValueError("sweep_stale needs a timezone-aware `before`")
        if before > datetime.now(UTC):
            raise ValueError(
                "sweep_stale refuses a cutoff in the future; it would treat "
                "every document as stale"
            )
        return self._sweep_stale(before)

    def read_document(self, document_id: str) -> DocumentProbe | None:
        """What the destination holds for one document, or None if nothing.
        Requires `can_read_back`; without it, reconcile is `unverifiable`."""
        self._require(self.capabilities.can_read_back, "can_read_back")
        return self._read_document(document_id)

    def holds_agency_content(self) -> bool:
        """True when the destination holds any document of the bound agency.

        F21's probe: a `first_run` decision is refused against a destination
        that already holds content, because a missing manifest in a mature
        deployment means something deleted it, and a cold start would then
        republish the corpus and permanently orphan every document CKB
        deleted while it was gone. Requires `can_list`. A non-listable sink
        raises NotSupported, and the coordinator falls back to read-back
        probes or reports the check unverifiable.
        """
        self._require(self.capabilities.can_list, "can_list")
        return self._holds_agency_content()

    # -- sink hooks --------------------------------------------------------

    @abstractmethod
    def _upsert_document(
        self, record: DocumentRecord, chunks: tuple[Chunk, ...] | None
    ) -> UpsertOutcome:
        """Publish, meeting F3. `chunks` is already validated."""

    @abstractmethod
    def _delete_documents(self, document_ids: tuple[str, ...]) -> DeleteOutcome[str]:
        """Delete de-duplicated, non-empty `document_ids`, meeting F4."""

    @abstractmethod
    def _delete_chunks(
        self, document_id: str, ordinals: tuple[int, ...]
    ) -> DeleteOutcome[int]:
        """Delete sorted, de-duplicated, non-negative `ordinals`."""

    @abstractmethod
    def _delete_agency(self) -> DeleteOutcome[str]:
        """Delete everything for the bound agency. Capability already checked."""

    @abstractmethod
    def _sweep_stale(self, before: datetime) -> DeleteOutcome[str]:
        """Delete the bound agency's documents synced before `before`. A sink
        with no way to do this raises NotSupported."""

    @abstractmethod
    def _read_document(self, document_id: str) -> DocumentProbe | None:
        """Read-back for one document. Capability already checked."""

    @abstractmethod
    def _holds_agency_content(self) -> bool:
        """Whether any of the bound agency's documents is present. Capability
        already checked. Stop at the first document found: this is a probe,
        not a count."""

    # -- helpers -----------------------------------------------------------

    @property
    def _reporter(self) -> str:
        return f"sink {self._sink_id!r}"

    def _checked_upsert(
        self,
        record: DocumentRecord,
        chunks: tuple[Chunk, ...] | None,
        outcome: UpsertOutcome,
    ) -> UpsertOutcome:
        """`outcome`, checked to describe the upsert that was asked for.

        Only PUBLISHED enters the manifest (F18), so a sink that claimed a
        document it had not fully written would leave the manifest vouching
        for content the destination does not hold.
        """
        expected = 0 if chunks is None else len(chunks)
        problem = None
        if outcome.document_id != record.document_id:
            problem = f"names document {outcome.document_id!r}"
        elif outcome.metadata_only != (chunks is None):
            problem = f"says metadata_only={outcome.metadata_only}"
        elif outcome.chunks_written > expected:
            problem = f"claims {outcome.chunks_written} of {expected} chunks written"
        elif outcome.published and outcome.chunks_written != expected:
            problem = (
                f"is published with {outcome.chunks_written} of {expected} "
                "chunks written"
            )
        if problem is not None:
            raise StoreError(
                f"{self._reporter}: the upsert outcome for document "
                f"{record.document_id!r} {problem}; refusing to trust it"
            )
        return outcome

    @staticmethod
    def _require(declared: bool, name: str) -> None:
        """NotSupported unless the capability `name` is declared."""
        if not declared:
            raise NotSupported(f"this sink does not declare {name}")

    def _require_bound_agency(self, agency_id: str) -> None:
        if agency_id != self._agency_id:
            raise ValueError(
                f"this sink is bound to agency {self._agency_id!r}; refusing an "
                f"agency-scoped operation for {agency_id!r}"
            )
