"""The manifest service — load, build and commit one agency's manifest
(F1, F6, F17).

The manifest IS the diff's previous state; there is no second state file.
`ManifestStore` (D16–D20) reads and writes the bytes and refuses anything it
could not parse back. This module decides what those bytes mean for a run:

- LOAD (F6, F17). An absent manifest is a first run only when
  DIFF_ON_MISSING_MANIFEST=first_run; under `fail` it is `ManifestMissing`,
  so the policy is greppable rather than something "reprocess everything" can
  be configured into. A manifest written for another destination is
  `SinkMismatch`: it claims published work that does not exist at this one,
  and reading it as a first run or as `unchanged` would leave a fresh
  destination permanently empty while every run reported success. Store
  errors and `ManifestUnreadable` propagate untouched (D20).
- BUILD (F1). The next manifest from diff.record()'s documents. Two rules are
  enforced here because this is the last point before they become durable:
  only a `finished` row may have its `source_updated_at` recorded — recording
  it for content still in flight makes Gate 2 skip the very row that would
  carry the finished text, and that is the one failure in this area that never
  self-heals (cleaning's catch_error re-raises without touching `status`) —
  and `corpus_watermark` is withheld unless every document that should have
  published did.
- COMMIT (F6). The single commit point: one atomic PUT. Everything a run did
  before it is replayable, and nothing after it may fail the run.

All three refusals are StoreError subclasses, so the coordinator's one failure
path aborts the agency and commits nothing on every one of them.
"""

import logging
from collections.abc import Mapping
from typing import Literal, get_args

from exporter.core.constants import (
    MANIFEST_DOCUMENT_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
)
from exporter.core.diff import DiffResult
from exporter.core.schemas import (
    AgencyWatermark,
    Manifest,
    ManifestDocumentEntry,
    StoreObject,
)
from exporter.services.manifest_store import ManifestStore
from exporter.stores.base import StoreError

__all__ = [
    "ManifestMissing",
    "ManifestService",
    "MissingManifestPolicy",
    "SinkMismatch",
]

logger = logging.getLogger(__name__)

MissingManifestPolicy = Literal["first_run", "fail"]

# The only row status whose updated_at a manifest entry may record (F1).
_TERMINAL_STATUS = "finished"


class ManifestMissing(StoreError):  # noqa: N818
    """No manifest, and DIFF_ON_MISSING_MANIFEST=fail. Never a first run.
    Permanent: the next tick finds the same absence."""

    def __init__(self, message: str) -> None:
        super().__init__(message, permanent=True)


class SinkMismatch(StoreError):  # noqa: N818
    """The manifest describes another destination (F17). Switching sinks is a
    deliberate re-bootstrap against a fresh manifest, and this is what makes
    an accidental one loud."""

    def __init__(self, message: str) -> None:
        super().__init__(message, permanent=True)


class ManifestService:
    """Manifest load, build and commit for one sink."""

    def __init__(
        self,
        store: ManifestStore,
        *,
        sink_id: str,
        on_missing: MissingManifestPolicy,
    ) -> None:
        if on_missing not in get_args(MissingManifestPolicy):
            raise ValueError(f"unknown missing-manifest policy {on_missing!r}")
        if not isinstance(sink_id, str) or not sink_id:
            raise ValueError(f"sink_id must be a non-empty string, got {sink_id!r}")
        self._store = store
        self._sink_id = sink_id
        self._on_missing = on_missing

    @property
    def sink_id(self) -> str:
        return self._sink_id

    def load_previous(self, agency_id: str) -> Manifest | None:
        """The committed manifest, None for a first run, or raise."""
        manifest = self._store.load(agency_id)
        if manifest is None:
            if self._on_missing == "fail":
                raise ManifestMissing(
                    f"no manifest at {self._store.key_for(agency_id)!r} and "
                    "DIFF_ON_MISSING_MANIFEST=fail. A first run here would "
                    "republish the corpus and orphan every document deleted "
                    "while the manifest was gone; set first_run only for a "
                    "deliberate bootstrap."
                )
            return None
        if manifest.sink_id != self._sink_id:
            # Both are sink names, never secrets, so naming them is safe.
            raise SinkMismatch(
                f"manifest at {self._store.key_for(agency_id)!r} describes sink "
                f"{manifest.sink_id!r}, but this deployment runs "
                f"{self._sink_id!r}; refusing to diff against it"
            )
        return manifest

    def build(
        self,
        *,
        agency_id: str,
        diff: DiffResult,
        documents: Mapping[str, ManifestDocumentEntry],
        watermark: AgencyWatermark,
        previous: Manifest | None,
        advance_watermark: bool,
        committed_at: str,
    ) -> Manifest:
        """The next manifest. `documents` is diff.record()'s output.

        `advance_watermark` says the run did everything the rows asked of it:
        every new, content_changed and metadata_changed document published,
        every tail confirmed deleted, no document skipped by a guard whose
        verdict depends on configuration rather than on the row, and no entry
        left unconfirmed. When it is False the watermark is
        withheld — null, exactly as on an unfinished bootstrap — so the next
        run cannot take Gate 1's shortcut and walks every row, with Gate 2
        sparing the unchanged ones at zero object reads. Carrying the previous
        watermark instead would not be safe: after a fingerprint change it can
        equal the current one, and Gate 1 would then skip documents still on
        the old geometry. F15 and F20 add their own withholding conditions
        here.
        """
        if diff.first_run != (previous is None):
            raise ValueError("the diff and the previous manifest disagree on first run")
        if not isinstance(committed_at, str) or not committed_at:
            raise ValueError("committed_at must be a non-empty string")
        before = {} if previous is None else previous.documents
        for document_id, entry in documents.items():
            # Only an updated_at this run records is checked. An entry carried
            # from the previous manifest was judged when it was written, and
            # refusing it now would refuse every run, committing nothing,
            # until someone edited the manifest by hand.
            prior = before.get(document_id)
            if prior is not None and prior.source_updated_at == entry.source_updated_at:
                continue
            if entry.source_status != _TERMINAL_STATUS:
                raise ValueError(
                    f"document {document_id}: refusing to record a row in status "
                    f"{entry.source_status!r}; only a {_TERMINAL_STATUS!r} row's "
                    "updated_at may be recorded (F1)"
                )
        return Manifest(
            schema_version=MANIFEST_DOCUMENT_SCHEMA_VERSION,
            manifest_schema_version=MANIFEST_SCHEMA_VERSION,
            agency_id=agency_id,
            sink_id=self._sink_id,
            corpus_watermark=watermark.max_updated_at if advance_watermark else None,
            document_count=watermark.live_count,
            chunker_fingerprint=diff.chunker_fingerprint,
            committed_at=committed_at,
            documents=dict(documents),
        )

    def commit(self, manifest: Manifest) -> StoreObject:
        """The single commit point (F6)."""
        if manifest.sink_id != self._sink_id:
            raise ValueError("refusing to commit a manifest built for another sink")
        return self._store.commit(manifest)

    def mirror_deletion_journal(self, run_id: str, payload: bytes) -> StoreObject:
        """Copy a committed run's local journal to the manifest store (F7)."""
        return self._store.mirror_deletion_journal(run_id, payload)
