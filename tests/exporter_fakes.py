"""In-memory fakes for the exporter's destination layers (Stage D).

Not a test module (no `test_` prefix), so pytest does not collect it; test
files import it directly, which works because tests/ has no __init__.py and
pytest puts it on sys.path.

- InMemoryStore is "the store ABC has one working implementation plus a
  fake" from Stage D's done-when. It records every backend call in order, so
  ordering assertions (F3/F4) read the call log rather than the code.
- RecordingSink is the minimal ContentSink over an InMemoryStore. It proves
  the D0 ABC is implementable independently of ObjectStoreSink, and it is the
  fake the sink conformance suite (D15) and Stage F's coordinator tests run
  against.
- ObservedStore wraps ANY store — including a live S3Store — with the same
  call log and failure injection InMemoryStore has, so the conformance suites
  observe a real endpoint exactly as they observe the fake.
- live_s3_store_or_skip builds an S3Store from CONFORMANCE_S3_* or skips the
  calling test. It is the only thing here that reaches a network, and only
  when those variables are set.
- FakeDocumentSource stands in for Stage E's Resql+S3 document source. It
  hashes and parses with the real core functions, so the coordinator sees
  exactly what a real read would hand it.
- patch_wiring points the worker task — and through it the CLI and the
  endpoint's child — at these fakes, with A17's agency check stubbed.
"""

import hashlib
import json
import os
import re
import threading
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

import pytest

from exporter.core.chunking import Chunker
from exporter.core.constants import CHUNK_PROFILES
from exporter.core.ids import (
    chunker_fingerprint,
    document_chunk_key,
    document_metadata_key,
)
from exporter.core.metadata import SidecarRejection, parse_sidecar
from exporter.core.schemas import Chunk, DocumentRecord, DocumentRef
from exporter.core.text_normaliser import normalise_nfc, sha256_hex, sha256_text
from exporter.services.document_source import (
    CorpusWatermark,
    DocumentSource,
    ReadDocument,
)
from exporter.services.manifest_service import ManifestService
from exporter.services.manifest_store import ManifestStore
from exporter.services.publish_service import PublishCoordinator
from exporter.services.run_state import RunStateStore
from exporter.sinks.base import (
    ContentSink,
    DocumentProbe,
    NotSupported,
    SinkCapabilities,
    UpsertOutcome,
    UpsertStatus,
)
from exporter.sinks.object_store_sink import roll_up
from exporter.stores.base import (
    DeleteOutcome,
    ExternalStoreClient,
    StoreCapabilities,
    StoreError,
    StoreObject,
)
from exporter.stores.s3_store import S3Store

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
    max_concurrency=16,
)

INJECTED_FAILURE = "InjectedFailure"

_CHUNK_NAME = re.compile(r"([0-9]{5})\.json")

# The variables that point the live conformance variants at a real endpoint.
CONFORMANCE_ENV = (
    "CONFORMANCE_S3_ENDPOINT_URL",
    "CONFORMANCE_S3_BUCKET",
    "CONFORMANCE_S3_ACCESS_KEY_ID",
    "CONFORMANCE_S3_SECRET_ACCESS_KEY",
)


@dataclass(frozen=True)
class StoredObject:
    payload: bytes
    content_type: str
    metadata: Mapping[str, str]


class InMemoryStore(ExternalStoreClient):
    """A dict-backed ExternalStoreClient with injectable failures.

    - `fail_keys`: get/exists/put on these raise StoreError — an outage, not
      an absence. Retryable.
    - `permanent_fail_keys`: the same, but the StoreError is permanent — a
      refused credential, a missing bucket (F19).
    - `fail_delete_keys`: delete_many reports these as failed, per item.
    - `unreported_delete_keys`: the backend "forgets" these in its outcome,
      which the base class must turn into failures.

    Thread-safe, because the coordinator upserts from worker threads (F16):
    every operation holds one lock, and a listing is a snapshot.
    """

    def __init__(
        self,
        *,
        capabilities: StoreCapabilities = FULL_STORE_CAPABILITIES,
        fail_keys: Iterable[str] = (),
        permanent_fail_keys: Iterable[str] = (),
        fail_delete_keys: Iterable[str] = (),
        unreported_delete_keys: Iterable[str] = (),
    ) -> None:
        self._capabilities = capabilities
        self.fail_keys = set(fail_keys)
        self.permanent_fail_keys = set(permanent_fail_keys)
        self.fail_delete_keys = set(fail_delete_keys)
        self.unreported_delete_keys = set(unreported_delete_keys)
        self.objects: dict[str, StoredObject] = {}
        # (verb, key-or-keys) in the order the backend saw them.
        self.calls: list[tuple[str, object]] = []
        self._lock = threading.Lock()

    @property
    def capabilities(self) -> StoreCapabilities:
        return self._capabilities

    def _check(self, key: str) -> None:
        if key in self.permanent_fail_keys:
            raise StoreError(f"{INJECTED_FAILURE} for {key}", permanent=True)
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
        with self._lock:
            self.calls.append(("put", key))
            self._check(key)
            self.objects[key] = StoredObject(payload, content_type, dict(metadata))
        etag = hashlib.sha256(payload).hexdigest()[:32]
        return StoreObject(key=key, size_bytes=len(payload), etag=etag)

    def _get_bytes(self, key: str) -> bytes | None:
        with self._lock:
            self.calls.append(("get", key))
            self._check(key)
            stored = self.objects.get(key)
        return None if stored is None else stored.payload

    def _exists(self, key: str) -> bool:
        with self._lock:
            self.calls.append(("exists", key))
            self._check(key)
            return key in self.objects

    def _list_keys(self, prefix: str) -> Iterator[StoreObject]:
        with self._lock:
            self.calls.append(("list", prefix))
            listed = [
                StoreObject(key=key, size_bytes=len(stored.payload))
                for key, stored in sorted(self.objects.items())
                if key.startswith(prefix)
            ]
        yield from listed

    def _delete_many(self, keys: tuple[str, ...]) -> DeleteOutcome[str]:
        deleted: list[str] = []
        missing: list[str] = []
        failed: dict[str, str] = {}
        with self._lock:
            self.calls.append(("delete", keys))
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


class ObservedStore(ExternalStoreClient):
    """Any store, with InMemoryStore's call log and failure injection.

    The log uses InMemoryStore's vocabulary — ("put", key), ("get", key),
    ("exists", key), ("list", prefix), ("delete", keys) — so one harness reads
    either. Writes and reads go to `inner` through its public verbs, so its own
    rules still apply.
    """

    def __init__(self, inner: ExternalStoreClient) -> None:
        self.inner = inner
        self.fail_keys: set[str] = set()
        self.fail_delete_keys: set[str] = set()
        self.calls: list[tuple[str, object]] = []

    @property
    def capabilities(self) -> StoreCapabilities:
        return self.inner.capabilities

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
        return self.inner.put_bytes(
            key, payload, content_type=content_type, metadata=metadata
        )

    def _get_bytes(self, key: str) -> bytes | None:
        self.calls.append(("get", key))
        self._check(key)
        return self.inner.get_bytes(key)

    def _exists(self, key: str) -> bool:
        self.calls.append(("exists", key))
        self._check(key)
        return self.inner.exists(key)

    def _list_keys(self, prefix: str) -> Iterator[StoreObject]:
        self.calls.append(("list", prefix))
        return self.inner.list_keys(prefix)

    def _delete_many(self, keys: tuple[str, ...]) -> DeleteOutcome[str]:
        self.calls.append(("delete", keys))
        failed = {key: INJECTED_FAILURE for key in keys if key in self.fail_delete_keys}
        attempted = self.inner.delete_many(key for key in keys if key not in failed)
        return DeleteOutcome.combine(attempted, DeleteOutcome(failed=failed))


def live_s3_store_or_skip(
    *, supports_batch_delete: bool = True, list_page_size: int = 1000
) -> S3Store:
    """An S3Store on the CONFORMANCE_S3_* endpoint, or skip the calling test.

    Point it at any S3-compatible store with an existing bucket, e.g.:
        docker run -p 9000:9000 minio/minio server /data
        docker run -p 8333:8333 chrislusf/seaweedfs server -s3
    """
    values = {name: os.environ.get(name, "") for name in CONFORMANCE_ENV}
    missing = [name for name, value in values.items() if not value]
    if missing:
        pytest.skip(f"live S3 conformance not configured ({', '.join(missing)})")
    return S3Store(
        endpoint_url=values["CONFORMANCE_S3_ENDPOINT_URL"],
        bucket=values["CONFORMANCE_S3_BUCKET"],
        access_key_id=values["CONFORMANCE_S3_ACCESS_KEY_ID"],
        secret_access_key=values["CONFORMANCE_S3_SECRET_ACCESS_KEY"],
        region=os.environ.get("CONFORMANCE_S3_REGION", "us-east-1"),
        supports_batch_delete=supports_batch_delete,
        list_page_size=list_page_size,
    )


def empty_prefix(store: ExternalStoreClient, prefix: str) -> None:
    """Delete everything under `prefix` — teardown for a live endpoint."""
    store.delete_many(obj.key for obj in store.list_keys(prefix))


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
        written = 0
        try:
            self.store.put_json(self._metadata_key(record.document_id), record)
            for chunk in chunks or ():
                self.store.put_json(
                    self._chunk_key(record.document_id, chunk.ordinal), chunk
                )
                written += 1
        except NotSupported:
            raise
        except StoreError as exc:
            # A failed write is an outcome, not an exception (F18): the
            # coordinator counts it and keeps the previous manifest entry.
            # Permanence is carried through, as ObjectStoreSink does, so the
            # systemic-failure abort (F19) sees what the store said.
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
        per_document: list[DeleteOutcome[str]] = []
        for document_id in document_ids:
            record_key = self._metadata_key(document_id)
            # Everything under the document except its record: chunks, and
            # any stray object a purge must not leave behind.
            outcome = self.store.delete_many(
                obj.key
                for obj in self.store.list_keys(record_key.rsplit("/", 1)[0] + "/")
                if obj.key != record_key
            )
            # F4: the record stays for as long as anything else of it might.
            if outcome.is_complete:
                outcome = DeleteOutcome.combine(
                    outcome, self.store.delete_many([record_key])
                )
            per_document.append(roll_up(document_id, outcome))
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
        # documents/ only: the manifest beside it belongs to the manifest store.
        prefix = f"{self.PREFIX}/agencies/{self.agency_id}/documents/"
        outcome = self.store.delete_many(
            obj.key for obj in self.store.list_keys(prefix)
        )
        return roll_up(self.agency_id, outcome)

    def _sweep_stale(self, before: datetime) -> DeleteOutcome[str]:
        raise NotSupported("RecordingSink keeps no synced_at index")

    def _holds_agency_content(self) -> bool:
        prefix = f"{self.PREFIX}/agencies/{self.agency_id}/documents/"
        # Parse, never count (D12): only a record or a chunk is content.
        return any(
            obj.key.endswith("/metadata.json")
            or _CHUNK_NAME.fullmatch(obj.key.rsplit("/", 1)[1]) is not None
            for obj in self.store.list_keys(prefix)
        )

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


def make_row(
    document_id: str,
    *,
    agency_id: str = "agency-1",
    updated_at: str = "2026-10-01T09:00:00Z",
    status: str = "finished",
    is_deleted: bool = False,
    is_excluded: bool = False,
    content_origin: str | None = "cleaned",
    source_base_id: str = "source-1",
) -> DocumentRef:
    """An unread row as list_agency_export_documents.sql would return it."""
    has_content = content_origin is not None
    return DocumentRef(
        document_id=document_id,
        source_base_id=source_base_id,
        agency_id=agency_id,
        url=f"https://www.eesti.ee/{document_id}",
        page_title=f"Title {document_id}",
        subsector="",
        status=status,
        is_excluded=is_excluded,
        is_deleted=is_deleted,
        updated_at=updated_at,
        content_key=f"uploads/{document_id}/{content_origin}.md"
        if has_content
        else None,
        metadata_key=(
            f"uploads/{document_id}/{content_origin}.meta.json" if has_content else None
        ),
        content_origin=content_origin,
        file_size=None,
    )


@dataclass
class FakeDocument:
    row: DocumentRef
    text: str
    sidecar: Mapping[str, object] | None
    skip_reason: str | None = None


class FakeDocumentSource(DocumentSource):
    """Rows plus each document's text and sidecar, with a read log and
    injectable read failures. A read normalises and hashes with the real
    core functions and parses the sidecar with the real parser."""

    def __init__(self) -> None:
        self.documents: dict[str, FakeDocument] = {}
        self.reads: list[str] = []
        self.fail_reads: set[str] = set()
        self.listed = 0

    def put(
        self,
        row: DocumentRef,
        text: str = "",
        *,
        sidecar: Mapping[str, object] | None = None,
        skip_reason: str | None = None,
    ) -> DocumentRef:
        """Add or replace a document. The default sidecar cites the row."""
        if sidecar is None:
            sidecar = {"source_url": row.url, "page_title": row.page_title}
        self.documents[row.document_id] = FakeDocument(row, text, sidecar, skip_reason)
        return row

    def remove(self, document_id: str) -> None:
        del self.documents[document_id]

    def watermark(self, agency_id: str) -> CorpusWatermark:
        rows = [d.row for d in self.documents.values() if d.row.agency_id == agency_id]
        return CorpusWatermark(
            max_updated_at=max((r.updated_at for r in rows), default=None),
            document_count=sum(1 for r in rows if not r.is_deleted),
        )

    def list_documents(self, agency_id: str) -> Sequence[DocumentRef]:
        self.listed += 1
        return [d.row for d in self.documents.values() if d.row.agency_id == agency_id]

    def read(self, ref: DocumentRef) -> ReadDocument:
        self.reads.append(ref.document_id)
        if ref.document_id in self.fail_reads:
            raise StoreError(f"{INJECTED_FAILURE} reading {ref.document_id}")
        document = self.documents[ref.document_id]
        if document.skip_reason is not None:
            return ReadDocument(
                replace(ref, skip_reason=document.skip_reason), None, None
            )
        data = None if document.sidecar is None else json.dumps(document.sidecar)
        parsed = parse_sidecar(
            None if data is None else data.encode("utf-8"),
            row_url=ref.url,
            require_sidecar=True,
        )
        if isinstance(parsed, SidecarRejection):
            return ReadDocument(replace(ref, skip_reason=parsed.value), None, None)
        raw = document.text.encode("utf-8")
        text = normalise_nfc(document.text)
        return ReadDocument(
            replace(
                ref,
                raw_sha256=sha256_hex(raw),
                content_sha256=sha256_text(text),
                metadata_sha256=parsed.metadata_sha256,
            ),
            text,
            parsed,
        )


MANIFEST_PREFIX = "manifests"


def patch_wiring(
    monkeypatch: pytest.MonkeyPatch,
    *,
    source: DocumentSource,
    sink: ContentSink,
    manifest_client: ExternalStoreClient,
    checkpoint_every: int = 250,
) -> None:
    """Make exporter.worker.tasks build its coordinator over these fakes, and
    stub A17's agency check, so the task, the CLI and the endpoint's child
    run end to end with no network. The compact profile keeps chunks small."""
    profile = CHUNK_PROFILES["compact"]

    def build(
        _settings: object,
        *,
        agency_id: str,
        work_dir: Path,
        run_state: RunStateStore | None,
        source_factory: object = None,
    ) -> PublishCoordinator:
        assert agency_id == sink.agency_id
        return PublishCoordinator(
            sink=sink,
            manifests=ManifestService(
                ManifestStore(manifest_client, prefix=MANIFEST_PREFIX),
                sink_id=sink.sink_id,
                on_missing="first_run",
            ),
            source=source,
            chunker=Chunker.from_profile(profile),
            chunker_fingerprint=chunker_fingerprint(profile),
            work_dir=work_dir,
            max_chunks_per_document=5000,
            bootstrap_checkpoint_every=checkpoint_every,
            concurrency=4,
            sink_failure_abort_threshold=25,
            run_state=run_state,
        )

    monkeypatch.setattr("exporter.worker.tasks.build_coordinator", build)
    monkeypatch.setattr("exporter.worker.tasks.require_single_agency", lambda _s: 1)
