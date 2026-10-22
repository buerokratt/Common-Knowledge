"""The composition root: Settings in, a PublishCoordinator out.

Both entry points — POST /export_agency_async (F9) and the run_sync CLI
(F10) — build their run here, so the two cannot assemble it differently.
This is the one module that turns configuration into services; the services
themselves take what they need as constructor arguments and never read a
settings object (the cross-cutting Config rule).

It never asks which sink is configured: sinks/factory.py does (A13), and the
manifest service takes the sink's id from the sink it is paired with.

THE DOCUMENT SOURCE IS NOT BUILT YET. Stage F was written against the
DocumentSource ABC and an in-memory fake; the Resql enumeration, the S3 reads
and the hashing are Stage E. Until it lands, `build_document_source` refuses
with a message naming it — the same idiom sinks/factory.py uses for a sink
with no implementation — so both entry points fail loudly and visibly (a
`failed` run, recorded in /health) rather than doing anything partial.
"""

from collections.abc import Callable
from pathlib import Path

from exporter.api.config import ConfigurationError, Settings
from exporter.core.chunking import Chunker
from exporter.core.ids import chunker_fingerprint
from exporter.services.document_source import DocumentSource
from exporter.services.manifest_service import ManifestService
from exporter.services.manifest_store import build_manifest_store
from exporter.services.publish_service import PublishCoordinator
from exporter.services.run_state import RunStateStore
from exporter.sinks.factory import build_sink

__all__ = ["SourceFactory", "build_coordinator", "build_document_source"]

SourceFactory = Callable[[Settings], DocumentSource]


def build_document_source(settings: Settings) -> DocumentSource:
    """CKB, as the coordinator reads it. Stage E (E1–E13) implements it."""
    raise ConfigurationError(
        "no DocumentSource implementation in this build yet (Stage E, "
        "E1–E13): the Resql enumeration and the S3 reads of CKB's bucket are "
        "not built, so there is nothing to export from."
    )


def build_coordinator(
    settings: Settings,
    *,
    agency_id: str,
    work_dir: Path,
    run_state: RunStateStore | None,
    source_factory: SourceFactory = build_document_source,
) -> PublishCoordinator:
    """One run's coordinator, bound to `agency_id`.

    `work_dir` is the RESOLVED work dir assert_work_dir_usable() returned,
    not settings.content_work_dir: the lock, the journal and the last-run
    record must land where A14 checked. `source_factory` is the seam the
    tests use until Stage E provides the real source.
    """
    # The source first: until Stage E lands, its refusal is the one to read.
    source = source_factory(settings)
    sink = build_sink(settings, agency_id=agency_id)
    profile = settings.resolved_chunk_profile
    return PublishCoordinator(
        sink=sink,
        manifests=ManifestService(
            build_manifest_store(settings),
            sink_id=sink.sink_id,
            on_missing=settings.diff_on_missing_manifest,
        ),
        source=source,
        chunker=Chunker.from_profile(profile),
        chunker_fingerprint=chunker_fingerprint(profile),
        work_dir=work_dir,
        max_chunks_per_document=settings.max_chunks_per_document,
        bootstrap_checkpoint_every=settings.bootstrap_checkpoint_every,
        # F16: the coordinator caps this at the sink's max_concurrency.
        concurrency=settings.export_concurrency,
        sink_failure_abort_threshold=settings.sink_failure_abort_threshold,
        run_state=run_state,
    )
