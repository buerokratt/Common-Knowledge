"""A world for the deletion operations' tests (Stage J): one agency exported
through the real coordinator into an ObjectStoreSink over an InMemoryStore,
with the drain, reconcile and purge built over the same sink and manifest
store an export uses.

Not a test module (no `test_` prefix); tests import it directly, as they do
exporter_fakes.
"""

import itertools
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path

from exporter.core.chunking import Chunker
from exporter.core.constants import CHUNK_PROFILES
from exporter.core.ids import chunker_fingerprint
from exporter.core.schemas import Manifest, RunReport
from exporter.services.deletion_journal import DeletionJournal
from exporter.services.drain_service import DeletionDrain
from exporter.services.manifest_service import ManifestService
from exporter.services.manifest_store import ManifestStore
from exporter.services.operations import (
    DrainReport,
    OperationStateStore,
    PurgeReport,
    ReconcileReport,
)
from exporter.services.publish_service import PublishCoordinator
from exporter.services.purge_service import Purger
from exporter.services.reconcile_service import Reconciler
from exporter.sinks.base import ContentSink
from exporter.sinks.object_store_sink import ObjectStoreSink
from exporter_fakes import FakeDocumentSource, InMemoryStore, make_row

AGENCY = "agency-1"
PREFIX = "content"
MANIFEST_PREFIX = "manifests"
PROFILE = CHUNK_PROFILES["compact"]
FP = chunker_fingerprint(PROFILE)
EARLY = "2026-10-01T09:00:00Z"
LATER = "2026-10-08T09:00:00Z"


def text(sentences: int) -> str:
    """Estonian-ish prose long enough to chunk: ~45 characters a sentence."""
    return " ".join(
        f"Lause {i} räägib sellest, kuidas taotlus esitada." for i in range(sentences)
    )


def documents_prefix(document_id: str = "") -> str:
    return f"{PREFIX}/agencies/{AGENCY}/documents/{document_id}"


def metadata_key(document_id: str) -> str:
    return f"{documents_prefix(document_id)}/metadata.json"


def chunk_key(document_id: str, ordinal: int) -> str:
    return f"{documents_prefix(document_id)}/chunks/{ordinal:05d}.json"


@dataclass
class OpsWorld:
    tmp_path: Path
    store: InMemoryStore = field(default_factory=InMemoryStore)
    source: FakeDocumentSource = field(default_factory=FakeDocumentSource)
    manifest_client: InMemoryStore = field(default_factory=InMemoryStore)
    sink: ContentSink | None = None
    ticks: Iterator[int] = field(default_factory=itertools.count)

    def __post_init__(self) -> None:
        if self.sink is None:
            self.sink = ObjectStoreSink(
                store=self.store,
                prefix=PREFIX,
                agency_id=AGENCY,
                sink_id="object_store",
            )

    @property
    def the_sink(self) -> ContentSink:
        assert self.sink is not None
        return self.sink

    @property
    def work_dir(self) -> Path:
        return self.tmp_path / "work"

    def clock(self) -> str:
        n = next(self.ticks)
        return f"2026-10-19T10:{n // 60:02d}:{n % 60:02d}Z"

    def manifests(self) -> ManifestService:
        return ManifestService(
            ManifestStore(self.manifest_client, prefix=MANIFEST_PREFIX),
            sink_id=self.the_sink.sink_id,
            on_missing="first_run",
        )

    def manifest(self) -> Manifest | None:
        return ManifestStore(self.manifest_client, prefix=MANIFEST_PREFIX).load(AGENCY)

    # -- the export --------------------------------------------------------

    def export(self, run_id: str) -> RunReport:
        return PublishCoordinator(
            sink=self.the_sink,
            manifests=self.manifests(),
            source=self.source,
            chunker=Chunker.from_profile(PROFILE),
            chunker_fingerprint=FP,
            work_dir=self.work_dir,
            max_chunks_per_document=5000,
            bootstrap_checkpoint_every=250,
            concurrency=1,
            sink_failure_abort_threshold=25,
            clock=self.clock,
        ).export(run_id)

    def publish(self, *documents: tuple[str, str]) -> Manifest:
        """A first run publishing `documents` (id, text)."""
        for document_id, body in documents:
            self.source.put(make_row(document_id, updated_at=EARLY), body)
        report = self.export("run-0")
        assert report.outcome == "success", report
        manifest = self.manifest()
        assert manifest is not None
        return manifest

    def move(self, document_id: str, body: str | None = None, **row: object) -> None:
        """Move a row's updated_at, optionally changing its text or fields."""
        current = self.source.documents[document_id]
        self.source.put(
            replace(current.row, **{"updated_at": LATER, **row}),
            current.text if body is None else body,
            sidecar=dict(current.sidecar) if current.sidecar is not None else None,
        )

    def keys_of(self, document_id: str) -> list[str]:
        prefix = documents_prefix(document_id) + "/"
        return sorted(key for key in self.store.objects if key.startswith(prefix))

    def journal(self, run_id: str) -> DeletionJournal:
        return DeletionJournal(self.work_dir, run_id=run_id, agency_id=AGENCY)

    # -- the operations ----------------------------------------------------

    def drain(self, run_id: str = "drain-1", *, dry_run: bool = False) -> DrainReport:
        return DeletionDrain(
            sink=self.the_sink,
            manifests=self.manifests(),
            work_dir=self.work_dir,
            state=OperationStateStore(self.work_dir, "drain"),
            clock=self.clock,
        ).drain(run_id, dry_run=dry_run)

    def reconcile(
        self, run_id: str = "reconcile-1", *, repair: bool = False
    ) -> ReconcileReport:
        return Reconciler(
            sink=self.the_sink,
            manifests=self.manifests(),
            work_dir=self.work_dir,
            state=OperationStateStore(self.work_dir, "reconcile"),
            clock=self.clock,
        ).reconcile(run_id, repair=repair)

    def purge(self, run_id: str = "purge-1") -> PurgeReport:
        return Purger(
            sink=self.the_sink,
            manifests=self.manifests(),
            chunker_fingerprint=FP,
            work_dir=self.work_dir,
            state=OperationStateStore(self.work_dir, "purge"),
            clock=self.clock,
        ).purge(run_id, confirm=AGENCY)
