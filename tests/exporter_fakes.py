"""In-memory fakes for the exporter's destination layers (Stage D).

Not a test module (no `test_` prefix), so pytest does not collect it; test
files import it directly, which works because tests/ has no __init__.py and
pytest puts it on sys.path.

- InMemoryStore is "the store ABC has one working implementation plus a
  fake" from Stage D's done-when. It records every backend call in order, so
  ordering assertions (F3/F4) read the call log rather than the code.
- RecordingSink is the minimal ContentSink over an InMemoryStore. It proves
  the D0 ABC is implementable, and it is the fake the sink conformance suite
  (D15) and Stage F's coordinator tests will run against.

No network, no Docker, no boto3.
"""

import hashlib
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime

from exporter.core.ids import document_chunk_key, document_metadata_key
from exporter.core.schemas import Chunk, DocumentRecord
from exporter.sinks.base import (
    ContentSink,
    DocumentProbe,
    NotSupported,
    SinkCapabilities,
    UpsertOutcome,
    UpsertStatus,
)
from exporter.stores.base import (
    DeleteOutcome,
    ExternalStoreClient,
    StoreCapabilities,
    StoreError,
    StoreObject,
)

FULL_STORE_CAPABILITIES = StoreCapabilities(
    can_delete=True, can_list=True, can_read=True, supports_batch_delete=True
)

FULL_SINK_CAPABILITIES = SinkCapabilities(
    can_list=True,
    can_read_back=True,
    supports_metadata_only_merge=True,
    supports_batch_delete=True,
    atomic_per_document=False,
    supports_agency_scoped_delete=True,
)

INJECTED_FAILURE = "InjectedFailure"

_CHUNK_NAME = re.compile(r"(\d{5})\.json")


def _as_one_item(item: str, outcome: DeleteOutcome[str]) -> DeleteOutcome[str]:
    """Roll the per-key result of deleting one document (or one agency) up to
    that one item: failed beats deferred beats deleted, and it is missing only
    when every key was already gone."""
    if outcome.failed:
        return DeleteOutcome(failed={item: "delete-incomplete"})
    if outcome.deferred:
        return DeleteOutcome(deferred=(item,))
    if outcome.deleted:
        return DeleteOutcome(deleted=(item,))
    return DeleteOutcome(missing=(item,))


@dataclass(frozen=True)
class StoredObject:
    payload: bytes
    content_type: str
    metadata: Mapping[str, str]


class InMemoryStore(ExternalStoreClient):
    """A dict-backed ExternalStoreClient with injectable failures.

    - `fail_keys`: get/exists/put on these raise StoreError — an outage, not
      an absence.
    - `fail_delete_keys`: delete_many reports these as failed, per item.
    - `unreported_delete_keys`: the backend "forgets" these in its outcome,
      which the base class must turn into failures.
    """

    def __init__(
        self,
        *,
        capabilities: StoreCapabilities = FULL_STORE_CAPABILITIES,
        fail_keys: Iterable[str] = (),
        fail_delete_keys: Iterable[str] = (),
        unreported_delete_keys: Iterable[str] = (),
    ) -> None:
        self._capabilities = capabilities
        self.fail_keys = set(fail_keys)
        self.fail_delete_keys = set(fail_delete_keys)
        self.unreported_delete_keys = set(unreported_delete_keys)
        self.objects: dict[str, StoredObject] = {}
        # (verb, key-or-keys) in the order the backend saw them.
        self.calls: list[tuple[str, object]] = []

    @property
    def capabilities(self) -> StoreCapabilities:
        return self._capabilities

    def _check(self, key: str) -> None:
        if key in self.fail_keys:
            raise StoreError(f"{INJECTED_FAILURE} for {key}")

    def _put_bytes(
        self,
        key: str,
        payload: bytes,
        *,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> StoreObject:
        self.calls.append(("put", key))
        self._check(key)
        self.objects[key] = StoredObject(payload, content_type, dict(metadata))
        etag = hashlib.sha256(payload).hexdigest()[:32]
        return StoreObject(key=key, size_bytes=len(payload), etag=etag)

    def _get_bytes(self, key: str) -> bytes | None:
        self.calls.append(("get", key))
        self._check(key)
        stored = self.objects.get(key)
        return None if stored is None else stored.payload

    def _exists(self, key: str) -> bool:
        self.calls.append(("exists", key))
        self._check(key)
        return key in self.objects

    def _list_keys(self, prefix: str) -> Iterator[StoreObject]:
        self.calls.append(("list", prefix))
        for key in sorted(self.objects):
            if key.startswith(prefix):
                yield StoreObject(key=key, size_bytes=len(self.objects[key].payload))

    def _delete_many(self, keys: tuple[str, ...]) -> DeleteOutcome[str]:
        self.calls.append(("delete", keys))
        deleted: list[str] = []
        missing: list[str] = []
        failed: dict[str, str] = {}
        for key in keys:
            if key in self.unreported_delete_keys:
                continue
            if key in self.fail_delete_keys:
                failed[key] = INJECTED_FAILURE
            elif self.objects.pop(key, None) is not None:
                deleted.append(key)
            else:
                missing.append(key)
        return DeleteOutcome(
            deleted=tuple(deleted), missing=tuple(missing), failed=failed
        )


class RecordingSink(ContentSink):
    """A minimal ContentSink over an InMemoryStore, using the real key
    builders. Meets F3 by writing the record before any chunk, and F4 by
    deleting chunks before the record."""

    PREFIX = "content"

    def __init__(
        self,
        *,
        agency_id: str = "agency-1",
        sink_id: str = "recording",
        store: InMemoryStore | None = None,
        capabilities: SinkCapabilities = FULL_SINK_CAPABILITIES,
    ) -> None:
        super().__init__(agency_id=agency_id, sink_id=sink_id)
        self.store = store if store is not None else InMemoryStore()
        self._capabilities = capabilities

    @property
    def capabilities(self) -> SinkCapabilities:
        return self._capabilities

    def _metadata_key(self, document_id: str) -> str:
        return document_metadata_key(self.PREFIX, self.agency_id, document_id)

    def _chunk_key(self, document_id: str, ordinal: int) -> str:
        return document_chunk_key(self.PREFIX, self.agency_id, document_id, ordinal)

    def _chunk_prefix(self, document_id: str) -> str:
        return self._metadata_key(document_id).rsplit("/", 1)[0] + "/chunks/"

    def _chunk_ordinals(self, document_id: str) -> list[int]:
        # Parse, never count: a gateway may list pseudo-directory entries (D12).
        names = (
            obj.key.rsplit("/", 1)[1]
            for obj in self.store.list_keys(self._chunk_prefix(document_id))
        )
        return [
            int(match[1]) for name in names if (match := _CHUNK_NAME.fullmatch(name))
        ]

    def _upsert_document(
        self, record: DocumentRecord, chunks: tuple[Chunk, ...] | None
    ) -> UpsertOutcome:
        self.store.put_json(self._metadata_key(record.document_id), record)
        for chunk in chunks or ():
            self.store.put_json(
                self._chunk_key(record.document_id, chunk.ordinal), chunk
            )
        return UpsertOutcome(
            document_id=record.document_id,
            status=UpsertStatus.PUBLISHED,
            chunks_written=len(chunks or ()),
            metadata_only=chunks is None,
        )

    def _delete_documents(self, document_ids: tuple[str, ...]) -> DeleteOutcome[str]:
        per_document: list[DeleteOutcome[str]] = []
        for document_id in document_ids:
            chunk_keys = [
                self._chunk_key(document_id, ordinal)
                for ordinal in self._chunk_ordinals(document_id)
            ]
            outcome = self.store.delete_many(chunk_keys)
            # F4: the record stays for as long as any chunk might.
            if outcome.is_complete:
                outcome = DeleteOutcome.combine(
                    outcome,
                    self.store.delete_many([self._metadata_key(document_id)]),
                )
            per_document.append(_as_one_item(document_id, outcome))
        return DeleteOutcome.combine(*per_document)

    def _delete_chunks(
        self, document_id: str, ordinals: tuple[int, ...]
    ) -> DeleteOutcome[int]:
        by_key = {
            self._chunk_key(document_id, ordinal): ordinal for ordinal in ordinals
        }
        outcome = self.store.delete_many(by_key)
        return DeleteOutcome(
            deleted=tuple(by_key[key] for key in outcome.deleted),
            missing=tuple(by_key[key] for key in outcome.missing),
            deferred=tuple(by_key[key] for key in outcome.deferred),
            failed={by_key[key]: reason for key, reason in outcome.failed.items()},
        )

    def _delete_agency(self) -> DeleteOutcome[str]:
        prefix = f"{self.PREFIX}/agencies/{self.agency_id}/"
        outcome = self.store.delete_many(
            obj.key for obj in self.store.list_keys(prefix)
        )
        return _as_one_item(self.agency_id, outcome)

    def _sweep_stale(self, before: datetime) -> DeleteOutcome[str]:
        raise NotSupported("RecordingSink keeps no synced_at index")

    def _read_document(self, document_id: str) -> DocumentProbe | None:
        record_present = self.store.exists(self._metadata_key(document_id))
        ordinals = tuple(self._chunk_ordinals(document_id))
        if not record_present and not ordinals:
            return None
        return DocumentProbe(
            document_id=document_id,
            record_present=record_present,
            chunk_count=len(ordinals),
            chunk_ordinals=ordinals,
        )
