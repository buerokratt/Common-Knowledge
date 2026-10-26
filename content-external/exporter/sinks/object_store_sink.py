"""The object-store sink — D0's verbs over an ExternalStoreClient (D14).

The coordinator never sees a key. This class owns everything that makes an
object store look like a destination for documents:

- THE KEY LAYOUT (D8). Keys come only from core/ids.py's builders:
      {prefix}/agencies/{agency_id}/documents/{document_id}/metadata.json
      {prefix}/agencies/{agency_id}/documents/{document_id}/chunks/{ordinal:05d}.json
  metadata.json sits BESIDE chunks/, not inside it, so the metadata-only path
  and the chunk path own disjoint key spaces and either can be replayed alone.
  The constructor checks that the builders' keys parse back with this module's
  parser, so a template change that the parser does not follow fails at
  construction rather than in a sweep.

- PARSE, NEVER COUNT (D12). Some S3-compatible gateways list pseudo-directory
  entries. Every listed key is parsed against the layout, and only a match for
  `metadata.json` or `chunks/([0-9]{5})\\.json` is read as a record or a
  chunk — counting entries would make a sweep treat a directory as a chunk
  and delete live content.

- PUBLISH ORDER (F3). metadata.json first, then chunks in ordinal order. No
  chunk is ever readable while its record is missing, including when a write
  fails part-way: a failed record write means no chunk was attempted.

- DELETE ORDER (F4). Everything under a document except its metadata.json
  goes first — its chunks, and any stray object that does not parse — and the
  metadata.json only once all of that is confirmed gone. So nothing references
  content that is already gone, and a purge reported complete has left
  nothing behind. Pseudo-directory entries (keys ending in "/") are skipped:
  they are a gateway's rendering of a prefix, not objects.

- DELETES REPORT, NOT RAISE, ONCE STARTED. If the store fails part-way
  through `delete_documents`, the documents already handled keep their
  outcomes and the failing one and every one after it are reported failed, so
  the deferred-deletion journal retries exactly those. Deleting needs a
  listing to find a document's chunks, so a store without `can_list` refuses
  `delete_documents` with NotSupported before touching anything; such a store
  can still take tail deletes, which arrive as coordinates.

- TAIL DELETES BY COORDINATE (C16). `delete_chunks` receives (document_id,
  ordinals) and renders keys here; the diff never sees a key.

WHAT IT NEVER TOUCHES. `manifest.json` and `_control/` belong to the manifest
store (D16). When that store is inherited (D17) they share this prefix, so
`delete_agency` is scoped to `documents/` and cannot remove the manifest that
records the purge.

`sweep_stale` is not supported. A listable sink sweeps orphans by the ordinal
tail rule — list a document's chunks, drop ordinals >= chunk_count (J5) — which
needs no timestamp. The `synced_at` sweep is the non-listable path (L10).
"""

import re
from collections.abc import Hashable
from datetime import datetime

from exporter.core.ids import (
    document_chunk_key,
    document_metadata_key,
    validate_prefix,
)
from exporter.core.schemas import Chunk, DocumentRecord
from exporter.sinks.base import (
    ContentSink,
    DocumentProbe,
    NotSupported,
    SinkCapabilities,
    StoreError,
    UpsertOutcome,
    UpsertStatus,
)
from exporter.stores.base import DeleteOutcome, ExternalStoreClient

__all__ = ["MAX_CONCURRENCY", "NOT_ATTEMPTED_REASON", "ObjectStoreSink", "roll_up"]

# The key remainder after `.../documents/`: one document segment, then either
# its record or one five-digit chunk. Anything else is not ours to count.
# [0-9], not \d: \d also matches non-ASCII digits, and a listed key that
# parsed as an ordinal would be "deleted" through a rebuilt ASCII key that
# does not exist.
_DOCUMENT_KEY = re.compile(
    r"(?P<document_id>[^/]+)/(?:metadata\.json|chunks/(?P<ordinal>[0-9]{5})\.json)"
)

_LAYOUT_PROBE_DOCUMENT = "layout-probe"
_LAYOUT_PROBE_ORDINAL = 7

# How many distinct per-key reasons a rolled-up failure carries.
_MAX_ROLLED_UP_REASONS = 3

# Reported for each document a failed store call left unattempted.
NOT_ATTEMPTED_REASON = "not-attempted-after-store-error"

# F16. An object store absorbs 8-16 parallel writers without self-inflicted
# throttling, and each in-flight document is a handful of small PUTs. The top
# of that range, so EXPORT_CONCURRENCY (itself capped at 16) is what decides.
MAX_CONCURRENCY = 16


def roll_up[T: Hashable](item: T, outcome: DeleteOutcome[str]) -> DeleteOutcome[T]:
    """Collapse a per-key outcome into one answer for `item` (a document id,
    or the agency id for a whole-agency delete).

    Failed beats deferred beats deleted, and `missing` only when every key was
    already gone — so a document is reported deleted only when nothing of it
    could still be partially present. A failure keeps the keys' own reasons
    (error codes), so the journal says why and not only that.
    """
    if outcome.failed:
        reasons = sorted(set(outcome.failed.values()))[:_MAX_ROLLED_UP_REASONS]
        return DeleteOutcome(failed={item: f"delete-incomplete: {', '.join(reasons)}"})
    if outcome.deferred:
        return DeleteOutcome(deferred=(item,))
    if outcome.deleted:
        return DeleteOutcome(deleted=(item,))
    return DeleteOutcome(missing=(item,))


class ObjectStoreSink(ContentSink):
    """Documents as blobs: one metadata.json plus one blob per chunk."""

    def __init__(
        self,
        *,
        store: ExternalStoreClient,
        prefix: str,
        agency_id: str,
        sink_id: str,
    ) -> None:
        super().__init__(agency_id=agency_id, sink_id=sink_id)
        self._store = store
        self._prefix = validate_prefix(prefix)
        probe_key = self._metadata_key(_LAYOUT_PROBE_DOCUMENT)
        suffix = f"{_LAYOUT_PROBE_DOCUMENT}/metadata.json"
        if not probe_key.endswith("/" + suffix):
            raise ValueError(
                f"unexpected metadata key layout {probe_key!r}; this sink's "
                "parser and core/constants.py's key templates have diverged"
            )
        self._documents_prefix = probe_key[: -len(suffix)]
        self._assert_layout_round_trips()

    @property
    def store(self) -> ExternalStoreClient:
        """The store underneath. For tests and the conformance harness; the
        coordinator never reaches for it."""
        return self._store

    @property
    def capabilities(self) -> SinkCapabilities:
        """Derived from the store, honestly: every capability that needs a
        listing is only as real as the store's `can_list`. D0 has no field
        for whole-document deletes, which also need one — `can_list=False`
        is the declaration that `delete_documents` raises NotSupported."""
        store = self._store.capabilities
        return SinkCapabilities(
            can_list=store.can_list,
            can_read_back=store.can_read and store.can_list,
            supports_metadata_only_merge=True,
            supports_batch_delete=store.supports_batch_delete,
            atomic_per_document=False,
            supports_agency_scoped_delete=store.can_list,
            max_concurrency=MAX_CONCURRENCY,
        )

    # -- layout ------------------------------------------------------------

    def _metadata_key(self, document_id: str) -> str:
        return document_metadata_key(self._prefix, self.agency_id, document_id)

    def _chunk_key(self, document_id: str, ordinal: int) -> str:
        return document_chunk_key(self._prefix, self.agency_id, document_id, ordinal)

    def _document_prefix(self, document_id: str) -> str:
        return f"{self._documents_prefix}{document_id}/"

    def _chunk_prefix(self, document_id: str) -> str:
        return f"{self._document_prefix(document_id)}chunks/"

    def _parse(self, key: str) -> tuple[str, int | None] | None:
        """(document_id, None) for a record, (document_id, ordinal) for a
        chunk, None for anything else — a pseudo-directory, a stray object."""
        if not key.startswith(self._documents_prefix):
            return None
        match = _DOCUMENT_KEY.fullmatch(key[len(self._documents_prefix) :])
        if match is None:
            return None
        ordinal = match["ordinal"]
        return match["document_id"], None if ordinal is None else int(ordinal)

    def _assert_layout_round_trips(self) -> None:
        document, ordinal = _LAYOUT_PROBE_DOCUMENT, _LAYOUT_PROBE_ORDINAL
        if self._parse(self._metadata_key(document)) != (document, None) or self._parse(
            self._chunk_key(document, ordinal)
        ) != (document, ordinal):
            raise ValueError(
                "the object-store sink cannot parse the keys core/ids.py "
                "builds; update its parser alongside the key templates"
            )

    def _chunk_ordinals(self, document_id: str) -> list[int]:
        ordinals: list[int] = []
        for obj in self._store.list_keys(self._chunk_prefix(document_id)):
            parsed = self._parse(obj.key)
            if (
                parsed is not None
                and parsed[0] == document_id
                and parsed[1] is not None
            ):
                ordinals.append(parsed[1])
        return sorted(ordinals)

    # -- hooks -------------------------------------------------------------

    def _upsert_document(
        self, record: DocumentRecord, chunks: tuple[Chunk, ...] | None
    ) -> UpsertOutcome:
        written = 0
        try:
            # F3: the record first. If this fails, no chunk is attempted.
            self._store.put_json(self._metadata_key(record.document_id), record)
            for chunk in chunks or ():
                self._store.put_json(
                    self._chunk_key(record.document_id, chunk.ordinal), chunk
                )
                written += 1
        except NotSupported:
            raise
        except StoreError as exc:
            # The record stays, possibly with fewer chunks than it names. That
            # is safe for F3 (nothing readable lacks its record) and the
            # manifest does not advance, so the next run rewrites the same
            # keys — idempotent by construction.
            return UpsertOutcome(
                document_id=record.document_id,
                status=(
                    UpsertStatus.FAILED_PERMANENT
                    if exc.permanent
                    else UpsertStatus.FAILED_RETRYABLE
                ),
                chunks_written=written,
                metadata_only=chunks is None,
                detail=str(exc),
            )
        return UpsertOutcome(
            document_id=record.document_id,
            status=UpsertStatus.PUBLISHED,
            chunks_written=written,
            metadata_only=chunks is None,
        )

    def _delete_documents(self, document_ids: tuple[str, ...]) -> DeleteOutcome[str]:
        if not self._store.capabilities.can_list:
            raise NotSupported(
                "deleting a whole document needs a listing to find its chunks, "
                "and this sink's store does not declare can_list"
            )
        per_document: list[DeleteOutcome[str]] = []
        for index, document_id in enumerate(document_ids):
            try:
                per_document.append(self._delete_document(document_id))
            except NotSupported:
                raise
            except StoreError as exc:
                # Keep what is already done; journal the rest (module docstring).
                per_document.append(DeleteOutcome(failed={document_id: str(exc)}))
                per_document.append(
                    DeleteOutcome(
                        failed=dict.fromkeys(
                            document_ids[index + 1 :], NOT_ATTEMPTED_REASON
                        )
                    )
                )
                break
        return DeleteOutcome.combine(*per_document)

    def _delete_document(self, document_id: str) -> DeleteOutcome[str]:
        """One document: everything but its record, then the record (F4)."""
        record_key = self._metadata_key(document_id)
        contents = [
            obj.key
            for obj in self._store.list_keys(self._document_prefix(document_id))
            if obj.key != record_key and not obj.key.endswith("/")
        ]
        outcome = self._store.delete_many(contents)
        # F4: the record stays for as long as anything else of it might.
        if outcome.is_complete:
            outcome = DeleteOutcome.combine(
                outcome, self._store.delete_many([record_key])
            )
        return roll_up(document_id, outcome)

    def _delete_chunks(
        self, document_id: str, ordinals: tuple[int, ...]
    ) -> DeleteOutcome[int]:
        by_key = {
            self._chunk_key(document_id, ordinal): ordinal for ordinal in ordinals
        }
        outcome = self._store.delete_many(by_key)
        return DeleteOutcome(
            deleted=tuple(by_key[key] for key in outcome.deleted),
            missing=tuple(by_key[key] for key in outcome.missing),
            deferred=tuple(by_key[key] for key in outcome.deferred),
            failed={by_key[key]: reason for key, reason in outcome.failed.items()},
        )

    def _delete_agency(self) -> DeleteOutcome[str]:
        """Everything under `documents/` — every non-record object first
        (chunks and strays alike), then the records of documents whose other
        objects all went. Never the manifest or _control/."""
        contents: list[str] = []
        # Object key -> the document segment it sits under, if any.
        owner: dict[str, str] = {}
        metadata_keys: dict[str, str] = {}
        for obj in self._store.list_keys(self._documents_prefix):
            if obj.key.endswith("/"):
                continue
            parsed = self._parse(obj.key)
            if parsed is not None and parsed[1] is None:
                metadata_keys[parsed[0]] = obj.key
                continue
            contents.append(obj.key)
            remainder = obj.key[len(self._documents_prefix) :]
            if "/" in remainder:
                owner[obj.key] = remainder.split("/", 1)[0]

        # A failure here raises with nothing deleted, so raising loses nothing.
        contents_outcome = self._store.delete_many(contents)
        blocked = {owner[key] for key in contents_outcome.unresolved if key in owner}
        try:
            record_outcome = self._store.delete_many(
                key
                for document_id, key in metadata_keys.items()
                if document_id not in blocked
            )
        except NotSupported:
            raise
        except StoreError as exc:
            # The contents are already gone; the agency is still not.
            return DeleteOutcome(failed={self.agency_id: str(exc)})
        # Records held back by an unfinished delete count as failed, so the
        # agency is not reported gone while any of it is still present.
        held_back: DeleteOutcome[str] = DeleteOutcome(
            failed={
                key: "contents-not-deleted"
                for document_id, key in metadata_keys.items()
                if document_id in blocked
            }
        )
        return roll_up(
            self.agency_id,
            DeleteOutcome.combine(contents_outcome, record_outcome, held_back),
        )

    def _sweep_stale(self, before: datetime) -> DeleteOutcome[str]:
        raise NotSupported(
            "the object-store sink sweeps orphans by the ordinal tail rule "
            "(read_document + delete_chunks, J5); the synced_at sweep is the "
            "non-listable sinks' path (L10)"
        )

    def _holds_agency_content(self) -> bool:
        """F21. The first listed key that parses as a record or a chunk
        answers; a pseudo-directory or a stray does not (D12). Scoped to
        `documents/`, so an inherited manifest beside it never counts."""
        return any(
            self._parse(obj.key) is not None
            for obj in self._store.list_keys(self._documents_prefix)
        )

    def _read_document(self, document_id: str) -> DocumentProbe | None:
        record_present = self._store.exists(self._metadata_key(document_id))
        ordinals = tuple(self._chunk_ordinals(document_id))
        if not record_present and not ordinals:
            return None
        return DocumentProbe(
            document_id=document_id,
            record_present=record_present,
            chunk_count=len(ordinals),
            chunk_ordinals=ordinals,
        )
