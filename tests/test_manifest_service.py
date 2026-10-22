"""F1, F6, F17 — the manifest service: load policy, sink guard, build rules."""

from dataclasses import replace

import pytest

from exporter.core.constants import CHUNK_PROFILES
from exporter.core.diff import DiffResult
from exporter.core.ids import chunker_fingerprint
from exporter.core.schemas import AgencyWatermark, Manifest, ManifestDocumentEntry
from exporter.services.manifest_service import (
    ManifestMissing,
    ManifestService,
    SinkMismatch,
)
from exporter.services.manifest_store import ManifestStore
from exporter.stores.base import StoreError
from exporter_fakes import InMemoryStore

AGENCY = "agency-1"
SINK = "object_store"
FP = chunker_fingerprint(CHUNK_PROFILES["azure_native"])
WATERMARK = AgencyWatermark(max_updated_at="2026-10-19T09:00:00Z", live_count=3)


def _service(
    client: InMemoryStore | None = None,
    *,
    sink_id: str = SINK,
    on_missing: str = "first_run",
) -> tuple[ManifestService, InMemoryStore]:
    client = client if client is not None else InMemoryStore()
    service = ManifestService(
        ManifestStore(client, prefix="manifests"),
        sink_id=sink_id,
        on_missing=on_missing,  # pyright: ignore[reportArgumentType]
    )
    return service, client


def _entry(status: str = "finished") -> ManifestDocumentEntry:
    return ManifestDocumentEntry(
        source_base_id="source-1",
        content_origin="cleaned",
        source_updated_at="2026-10-01T09:00:00Z",
        source_status=status,
        raw_sha256="a" * 64,
        content_sha256="b" * 64,
        metadata_sha256="c" * 64,
        file_size=None,
        chunk_count=2,
        chunker_fingerprint=FP,
        state="published",
        processed_at="2026-10-01T09:05:00Z",
    )


def _diff(*, first_run: bool) -> DiffResult:
    return DiffResult(
        first_run=first_run, fingerprint_changed=False, chunker_fingerprint=FP
    )


def _build(
    service: ManifestService,
    *,
    previous: Manifest | None = None,
    advance_watermark: bool = True,
    documents: dict[str, ManifestDocumentEntry] | None = None,
) -> Manifest:
    return service.build(
        agency_id=AGENCY,
        diff=_diff(first_run=previous is None),
        documents={"d1": _entry()} if documents is None else documents,
        watermark=WATERMARK,
        previous=previous,
        advance_watermark=advance_watermark,
        committed_at="2026-10-19T10:00:00Z",
    )


# --- load (F6, F17) --------------------------------------------------------


def test_no_manifest_is_a_first_run_under_first_run() -> None:
    service, _ = _service()
    assert service.load_previous(AGENCY) is None


def test_no_manifest_fails_under_fail() -> None:
    service, _ = _service(on_missing="fail")
    with pytest.raises(ManifestMissing, match="DIFF_ON_MISSING_MANIFEST=fail"):
        service.load_previous(AGENCY)


def test_manifest_missing_is_a_permanent_store_error() -> None:
    """A StoreError, so the coordinator's one failure path catches it."""
    assert issubclass(ManifestMissing, StoreError)
    assert ManifestMissing("x").permanent is True


def test_a_committed_manifest_loads_back() -> None:
    service, _ = _service()
    manifest = _build(service)
    service.commit(manifest)
    assert service.load_previous(AGENCY) == manifest


def test_a_manifest_for_another_sink_fails_and_is_not_a_first_run() -> None:
    """F17: never treated as absent, never treated as unchanged."""
    writer, client = _service(sink_id="llm_module")
    writer.commit(_build(writer))
    reader, _ = _service(client, sink_id=SINK)

    with pytest.raises(SinkMismatch) as excinfo:
        reader.load_previous(AGENCY)
    assert "llm_module" in str(excinfo.value)
    assert excinfo.value.permanent is True


def test_a_store_error_on_load_propagates() -> None:
    """D20: an outage is not an absence."""
    client = InMemoryStore()
    service, _ = _service(client)
    client.fail_keys.add(ManifestStore(client, prefix="manifests").key_for(AGENCY))
    with pytest.raises(StoreError):
        service.load_previous(AGENCY)


def test_an_unknown_policy_is_refused() -> None:
    with pytest.raises(ValueError, match="policy"):
        _service(on_missing="reprocess_all")


# --- build (F1) ------------------------------------------------------------


def test_build_stamps_the_sink_versions_fingerprint_and_count() -> None:
    service, _ = _service()
    manifest = _build(service)
    assert manifest.sink_id == SINK
    assert manifest.agency_id == AGENCY
    assert manifest.chunker_fingerprint == FP
    assert manifest.document_count == WATERMARK.live_count
    assert manifest.corpus_watermark == WATERMARK.max_updated_at
    assert dict(manifest.documents) == {"d1": _entry()}


def test_the_watermark_is_withheld_when_not_everything_published() -> None:
    """Null, not the previous value: after a fingerprint change the previous
    watermark can equal the current one, and Gate 1 would then skip the
    documents still on the old geometry."""
    service, _ = _service()
    previous = _build(service)
    manifest = _build(service, previous=previous, advance_watermark=False)
    assert previous.corpus_watermark is not None
    assert manifest.corpus_watermark is None


@pytest.mark.parametrize("status", ["scraping", "cleaning", "in_review", "failed"])
def test_build_refuses_to_record_a_row_still_in_flight(status: str) -> None:
    """F1: recording updated_at for content in flight makes Gate 2 skip the
    row that would carry the finished text — and that never self-heals."""
    service, _ = _service()
    with pytest.raises(ValueError, match="F1"):
        _build(service, documents={"d1": _entry(status)})


def test_build_refuses_an_in_flight_row_whose_updated_at_moved() -> None:
    service, _ = _service()
    previous = _build(service)
    moved = replace(_entry("cleaning"), source_updated_at="2026-10-08T09:00:00Z")
    with pytest.raises(ValueError, match="F1"):
        _build(service, previous=previous, documents={"d1": moved})


def test_build_carries_an_entry_it_did_not_write() -> None:
    """A carried entry was judged when it was written; refusing it now would
    refuse every run until the manifest was edited by hand."""
    service, _ = _service()
    legacy = {"d1": _entry("cleaning")}
    previous = replace(_build(service), documents=legacy)
    manifest = _build(service, previous=previous, documents=legacy)
    assert dict(manifest.documents) == legacy


def test_build_refuses_a_diff_that_disagrees_about_first_run() -> None:
    service, _ = _service()
    with pytest.raises(ValueError, match="first run"):
        service.build(
            agency_id=AGENCY,
            diff=_diff(first_run=False),
            documents={},
            watermark=WATERMARK,
            previous=None,
            advance_watermark=True,
            committed_at="2026-10-19T10:00:00Z",
        )


def test_commit_refuses_a_manifest_built_for_another_sink() -> None:
    service, _ = _service()
    with pytest.raises(ValueError, match="another sink"):
        service.commit(replace(_build(service), sink_id="llm_module"))


def test_commit_is_one_put_to_the_manifest_key() -> None:
    service, client = _service()
    service.commit(_build(service))
    puts = [call for call in client.calls if call[0] == "put"]
    assert puts == [("put", "manifests/agencies/agency-1/manifest.json")]
