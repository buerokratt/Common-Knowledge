"""The composition root: Settings in, a PublishCoordinator — or the pieces a
deletion operation needs — out.

Both entry points — POST /export_agency_async (F9) and the run_sync CLI
(F10) — build their run here, so the two cannot assemble it differently.
The deletion operations (Stage J: the drain, reconcile, purge) build theirs
here too, from the same sink and manifest service an export would get, so an
operation can never act on a different destination than the export it
follows. This is the one module that turns configuration into services; the
services themselves take what they need as constructor arguments and never
read a settings object (the cross-cutting Config rule).

It never asks which sink is configured: sinks/factory.py does (A13), and the
manifest service takes the sink's id from the sink it is paired with.

The document source is Stage E's CkbDocumentSource: the Resql enumeration
and the read-only S3 reads of CKB's bucket. Building it touches neither —
its S3 client is created on the first read — so a missing AWS_* value fails
the run that needs it, never /health. The deletion operations never build
one: none of them reads CKB.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from exporter.api.config import Settings
from exporter.core.chunking import Chunker
from exporter.core.ids import chunker_fingerprint
from exporter.services.document_source import CkbDocumentSource, DocumentSource
from exporter.services.manifest_service import ManifestService
from exporter.services.manifest_store import build_manifest_store
from exporter.services.publish_service import PublishCoordinator
from exporter.services.run_state import RunStateStore
from exporter.sinks.base import ContentSink
from exporter.sinks.factory import build_sink

__all__ = [
    "DeletionContext",
    "SourceFactory",
    "build_coordinator",
    "build_deletion_context",
    "build_document_source",
]

SourceFactory = Callable[[Settings], DocumentSource]


@dataclass(frozen=True)
class DeletionContext:
    """What every deletion operation acts through: the sink bound to one
    agency, the manifest service paired with it, and the chunker fingerprint
    the diff is computed against (purge's classify(), J7)."""

    sink: ContentSink
    manifests: ManifestService
    chunker_fingerprint: str


def build_document_source(settings: Settings) -> DocumentSource:
    """CKB, as the coordinator reads it (Stage E, E1–E13)."""
    return CkbDocumentSource(settings)


def build_deletion_context(
    settings: Settings, *, agency_id: str, run_id: str
) -> DeletionContext:
    """The sink, bound to `agency_id` for the run `run_id` (which the
    llm-module sink's Idempotency-Keys carry), and its manifest service."""
    sink = build_sink(settings, agency_id=agency_id, run_id=run_id)
    return DeletionContext(
        sink=sink,
        manifests=ManifestService(
            build_manifest_store(settings),
            sink_id=sink.sink_id,
            on_missing=settings.diff_on_missing_manifest,
        ),
        chunker_fingerprint=chunker_fingerprint(settings.resolved_chunk_profile),
    )


def build_coordinator(
    settings: Settings,
    *,
    agency_id: str,
    run_id: str,
    work_dir: Path,
    run_state: RunStateStore | None,
    source_factory: SourceFactory = build_document_source,
) -> PublishCoordinator:
    """One run's coordinator, bound to `agency_id`, for the run `run_id`.

    `work_dir` is the RESOLVED work dir assert_work_dir_usable() returned,
    not settings.content_work_dir: the lock, the journal and the last-run
    record must land where A14 checked. `source_factory` is the seam the
    tests use to swap in a fake source.
    """
    source = source_factory(settings)
    context = build_deletion_context(settings, agency_id=agency_id, run_id=run_id)
    return PublishCoordinator(
        sink=context.sink,
        manifests=context.manifests,
        source=source,
        chunker=Chunker.from_profile(settings.resolved_chunk_profile),
        chunker_fingerprint=context.chunker_fingerprint,
        work_dir=work_dir,
        max_chunks_per_document=settings.max_chunks_per_document,
        bootstrap_checkpoint_every=settings.bootstrap_checkpoint_every,
        # F16: the coordinator caps this at the sink's max_concurrency.
        concurrency=settings.export_concurrency,
        sink_failure_abort_threshold=settings.sink_failure_abort_threshold,
        run_state=run_state,
        stale_sweep_enabled=settings.stale_sweep_enabled,
    )
