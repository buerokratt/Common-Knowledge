"""The diff: have I published this document before, and what changed about
it? Stage C.2 (C7-C16), Key Design #3.

Pure: no I/O, no clock, no settings. The caller loads the previous manifest
and hands it in, which is what makes this testable and what keeps the
fail-closed decision where a load error is actually visible.

Ported from RAG-Module-Internal/src/vector_indexer/diff_identifier/
(version_manager.py identify_comprehensive_changes(), diff_models.py
DiffResult). Ported, not imported, and the fork does not flow back. Where it
diverges, and why:

- Keyed on source_file.base_id, never on a content hash (C12). The ancestor
  maps files_map[file_hash] = relative_path, so two byte-identical documents
  collapse into one entry and a moved file classifies as unchanged. Here
  document_id is CKB's own stable key, so identical documents are distinct
  by construction and a "moved" document does not exist as a concept. The
  problem is designed out, not fixed, which is why tests/test_diff.py has no
  test for either case.
- Fails closed (C9). The ancestor catches every error and falls back to
  "treat everything as new", and an empty scan returns no deletions at all.
  Here there is no fallback and no try statement: previous=None is a genuine
  first run, a manifest that cannot be loaded never reaches this module, and
  an empty `current` against a non-empty manifest deletes everything. That is
  what a purge is, so "everything deleted" and "everything new" stay distinct.
- No DVC and no per-file logging (C10, C11). Nothing here logs; DiffResult's
  repr shows counts only, and error messages name a document id at most.
- The ancestor's `modified` is split into content_changed and
  metadata_changed, disjoint by construction (C13).
- Deletion is explicit (C14): is_deleted, is_excluded, status 'not_found' and
  absence from the row set. Nothing is inferred from an empty export.
- Skipped documents are held, never deleted (C15).
- chunks_to_delete holds (document_id, ordinal) coordinates (C16). The
  ancestor's {old_hash: path} map exists only because its consumer addresses
  chunks by content hash. Each destination renders coordinates its own way.

This module names no destination and no transport. The same rows and the
same manifest give the same DiffResult whichever destination is configured.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum

from exporter.core.schemas import DocumentRef, Manifest, ManifestDocumentEntry


class Bucket(StrEnum):
    """The six buckets. The values are also DiffResult's field names and the
    bucket names the run log counts by."""

    NEW = "new"
    CONTENT_CHANGED = "content_changed"
    METADATA_CHANGED = "metadata_changed"
    UNCHANGED = "unchanged"
    DELETED = "deleted"
    SKIPPED = "skipped"


class DeletionReason(StrEnum):
    """The four deletion signals (C14). Stable strings for the run report."""

    IS_DELETED = "is_deleted"
    IS_EXCLUDED = "is_excluded"
    # The page 404'd on refresh. CKB leaves the row live, not excluded and
    # with its content URL intact, so no other signal reports it (Finding 11).
    NOT_FOUND = "not_found"
    ABSENT_FROM_ROWS = "absent_from_rows"


class SkipReason(StrEnum):
    """The skip reasons this module derives from the row itself. Reasons
    found while reading (oversize, a bad sidecar) arrive on
    DocumentRef.skip_reason instead."""

    # status is anything but 'finished'. The content URL says nothing about
    # readiness: after the first clean it is always populated (Finding 11).
    NOT_READY = "not_ready"
    NO_CONTENT = "no_content"  # scraped, never cleaned


_READY_STATUS = "finished"
_NOT_FOUND_STATUS = "not_found"
# The only state a manifest entry may be in. An entry claims "this document
# is live at the destination"; anything else here is a bug in whatever wrote
# the manifest, and trusting it could hold or delete the wrong thing.
_PUBLISHED_STATE = "published"
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}")

# Which buckets require a previous manifest entry (True), forbid one (False)
# or take either (None).
_PREVIOUS_REQUIRED: dict[Bucket, bool | None] = {
    Bucket.NEW: False,
    Bucket.CONTENT_CHANGED: True,
    Bucket.METADATA_CHANGED: True,
    Bucket.UNCHANGED: True,
    Bucket.DELETED: True,
    Bucket.SKIPPED: None,
}


def _non_negative_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be an int >= 0, got {value!r}")
    return value


# --- C7: the models -----------------------------------------------------------


@dataclass(frozen=True)
class ChunkCoordinate:
    """Where a chunk sits: its document and its ordinal. Not a key — each
    destination turns a coordinate into whatever it addresses chunks by."""

    document_id: str
    ordinal: int

    def __post_init__(self) -> None:
        if not isinstance(self.document_id, str) or not self.document_id:
            raise ValueError("document_id must be a non-empty string")
        _non_negative_int("ordinal", self.ordinal)


@dataclass(frozen=True)
class DocumentDiff:
    """One document's verdict.

    `previous` is the manifest entry as last committed, or None when the
    document was never published. A held document carries it so the entry
    can be copied into the next manifest unchanged. `current` is the row,
    or None only when the row is gone from the row set altogether.
    """

    document_id: str
    bucket: Bucket
    reason: str | None  # why skipped or deleted; None in every other bucket
    current: DocumentRef | None = field(repr=False)
    previous: ManifestDocumentEntry | None = field(repr=False)

    def __post_init__(self) -> None:
        document_id = self.document_id
        if not isinstance(document_id, str) or not document_id:
            raise ValueError("document_id must be a non-empty string")
        bucket = Bucket(self.bucket)
        object.__setattr__(self, "bucket", bucket)

        if self.current is None:
            if (
                bucket is not Bucket.DELETED
                or self.reason != DeletionReason.ABSENT_FROM_ROWS
            ):
                raise ValueError(
                    f"document {document_id}: only a document absent from the "
                    "rows may have no current row"
                )
        elif self.current.document_id != document_id:
            raise ValueError(f"document {document_id}: current row is another one")

        required = _PREVIOUS_REQUIRED[bucket]
        if required is True and self.previous is None:
            raise ValueError(
                f"document {document_id}: {bucket} needs a previous manifest entry"
            )
        if required is False and self.previous is not None:
            raise ValueError(
                f"document {document_id}: {bucket} cannot have a previous entry"
            )

        if bucket is Bucket.DELETED:
            if self.reason not in set(DeletionReason):
                raise ValueError(
                    f"document {document_id}: deleted needs a DeletionReason, "
                    f"got {self.reason!r}"
                )
        elif bucket is Bucket.SKIPPED:
            if not isinstance(self.reason, str) or not self.reason:
                raise ValueError(f"document {document_id}: skipped needs a reason")
        elif self.reason is not None:
            raise ValueError(f"document {document_id}: {bucket} takes no reason")

        # The verdict must agree with the row it was reached from, so a
        # hand-built verdict cannot delete a live row or publish one that is
        # not ready.
        if self.current is not None:
            signal = _deletion_signal(self.current)
            if bucket is Bucket.DELETED:
                if signal != self.reason:
                    raise ValueError(
                        f"document {document_id}: deleted as {self.reason} but "
                        f"the row carries {signal}"
                    )
            elif signal is not None:
                raise ValueError(
                    f"document {document_id}: the row carries {signal}, so it "
                    "can only be deleted"
                )
            elif bucket is not Bucket.SKIPPED and not _ready(self.current):
                raise ValueError(
                    f"document {document_id}: {bucket} needs a finished row with "
                    "content and no skip_reason"
                )

    @property
    def held(self) -> bool:
        """Skipped, but published before: carried into the next manifest
        untouched, never published and never deleted (C15)."""
        return self.bucket is Bucket.SKIPPED and self.previous is not None


@dataclass(frozen=True, repr=False)
class DiffResult:
    """Every document in exactly one bucket, plus the chunk tails to delete.

    Each bucket is sorted by document_id, so the same input gives an equal
    result whatever order the rows arrived in. Rows with a deletion signal
    that were never published are in no bucket: there is nothing to delete,
    and counting them as deleted would make the deleted count nonzero on
    every run for as long as CKB keeps the soft-deleted row. They are
    counted in deleted_unpublished instead.
    """

    first_run: bool  # there was no previous manifest
    fingerprint_changed: bool  # always False on a first run
    chunker_fingerprint: str  # the current one, which the next manifest records
    new: tuple[DocumentDiff, ...] = ()
    content_changed: tuple[DocumentDiff, ...] = ()
    metadata_changed: tuple[DocumentDiff, ...] = ()
    unchanged: tuple[DocumentDiff, ...] = ()
    deleted: tuple[DocumentDiff, ...] = ()
    skipped: tuple[DocumentDiff, ...] = ()
    # Ordinals >= the new chunk_count of a content_changed document. Empty as
    # classify() returns it, because the new count only exists once the
    # document has been chunked, which happens after classification. Filled
    # by with_chunk_tails().
    chunks_to_delete: tuple[ChunkCoordinate, ...] = ()
    deleted_unpublished: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.chunker_fingerprint, str) or not _HEX_SHA256.fullmatch(
            self.chunker_fingerprint
        ):
            raise ValueError("chunker_fingerprint must be 64 lowercase hex characters")
        _non_negative_int("deleted_unpublished", self.deleted_unpublished)

        seen: set[str] = set()
        for bucket in Bucket:
            entries = tuple(getattr(self, bucket.value))
            object.__setattr__(self, bucket.value, entries)
            for entry in entries:
                if entry.bucket is not bucket:
                    raise ValueError(
                        f"document {entry.document_id} is {entry.bucket} but "
                        f"sits in {bucket}"
                    )
                if entry.document_id in seen:
                    raise ValueError(
                        f"document {entry.document_id} is in more than one bucket"
                    )
                seen.add(entry.document_id)

        if self.first_run:
            if self.fingerprint_changed:
                raise ValueError("a first run has no previous fingerprint to change")
            if self.content_changed or self.metadata_changed or self.unchanged:
                raise ValueError("a first run has nothing to compare against")
            if self.deleted or self.held:
                raise ValueError("a first run has nothing published to hold or delete")

        coordinates = tuple(self.chunks_to_delete)
        object.__setattr__(self, "chunks_to_delete", coordinates)
        changed = {entry.document_id: entry for entry in self.content_changed}
        if len(set(coordinates)) != len(coordinates):
            raise ValueError("chunks_to_delete names a chunk twice")
        for coordinate in coordinates:
            entry = changed.get(coordinate.document_id)
            if entry is None:
                raise ValueError(
                    f"chunks_to_delete names document {coordinate.document_id}, "
                    "which is not content_changed"
                )
            # Narrowing only: DocumentDiff.__post_init__ already refuses a
            # content_changed verdict with no previous entry, so this holds
            # under python -O too.
            assert entry.previous is not None
            if coordinate.ordinal >= entry.previous.chunk_count:
                raise ValueError(
                    f"chunks_to_delete names ordinal {coordinate.ordinal} of "
                    f"document {coordinate.document_id}, which was never published"
                )

    @classmethod
    def from_documents(
        cls,
        changes: Iterable[DocumentDiff | None],
        *,
        first_run: bool,
        fingerprint_changed: bool,
        chunker_fingerprint: str,
    ) -> "DiffResult":
        """Sort per-document verdicts into buckets. classify() builds its
        result this way, and so can a caller classifying one document at a
        time with classify_document(): pass every return value through
        unchanged, None included, and the None ones are counted as
        deleted_unpublished here rather than by the caller."""
        by_bucket: dict[Bucket, list[DocumentDiff]] = {bucket: [] for bucket in Bucket}
        deleted_unpublished = 0
        for change in changes:
            if change is None:
                deleted_unpublished += 1
            else:
                by_bucket[change.bucket].append(change)

        def ordered(bucket: Bucket) -> tuple[DocumentDiff, ...]:
            return tuple(sorted(by_bucket[bucket], key=lambda e: e.document_id))

        return cls(
            first_run=first_run,
            fingerprint_changed=fingerprint_changed,
            chunker_fingerprint=chunker_fingerprint,
            new=ordered(Bucket.NEW),
            content_changed=ordered(Bucket.CONTENT_CHANGED),
            metadata_changed=ordered(Bucket.METADATA_CHANGED),
            unchanged=ordered(Bucket.UNCHANGED),
            deleted=ordered(Bucket.DELETED),
            skipped=ordered(Bucket.SKIPPED),
            deleted_unpublished=deleted_unpublished,
        )

    def documents(self, bucket: Bucket) -> tuple[DocumentDiff, ...]:
        return getattr(self, Bucket(bucket).value)

    def counts(self) -> dict[str, int]:
        """Documents per bucket, keyed by bucket name — the shape the run log's
        terminal line takes."""
        return {bucket.value: len(self.documents(bucket)) for bucket in Bucket}

    def skipped_reasons(self) -> dict[str, str]:
        """document_id -> reason, for RunReport.skipped_reasons."""
        return {entry.document_id: entry.reason or "" for entry in self.skipped}

    @property
    def held(self) -> tuple[DocumentDiff, ...]:
        return tuple(entry for entry in self.skipped if entry.held)

    @property
    def is_purge(self) -> bool:
        """Every published document is to be deleted and nothing else is
        happening — what run_purge asks for, and also what an enumeration
        that wrongly came back empty would produce. The diff cannot tell
        those apart; this lets the caller refuse the second without an
        explicit request."""
        return bool(
            not self.first_run
            and self.deleted
            and not (
                self.new
                or self.content_changed
                or self.metadata_changed
                or self.unchanged
                or self.skipped
            )
        )

    def demote_to_skipped(self, document_id: str, reason: str) -> "DiffResult":
        """This result with one new or content_changed document moved to
        skipped, for a guard that can only run after chunking — too many
        chunks is the case. A document published before is held, so its
        live chunks stay exactly as they are, and any tail recorded for it
        is dropped."""
        if not isinstance(reason, str) or not reason:
            raise ValueError(f"document {document_id}: a skip needs a reason")
        for bucket in (Bucket.NEW, Bucket.CONTENT_CHANGED):
            entries = self.documents(bucket)
            match = next((e for e in entries if e.document_id == document_id), None)
            if match is None:
                continue
            demoted = DocumentDiff(
                document_id, Bucket.SKIPPED, reason, match.current, match.previous
            )
            return replace(
                self,
                **{bucket.value: tuple(e for e in entries if e is not match)},
                skipped=tuple(
                    sorted((*self.skipped, demoted), key=lambda e: e.document_id)
                ),
                chunks_to_delete=tuple(
                    c for c in self.chunks_to_delete if c.document_id != document_id
                ),
            )
        raise ValueError(
            f"document {document_id} was not chunked in this diff: only new and "
            "content_changed documents can be demoted"
        )

    def with_chunk_tails(
        self, new_chunk_counts: Mapping[str, int], *, require_complete: bool = True
    ) -> "DiffResult":
        """This result with chunks_to_delete filled in for the documents in
        `new_chunk_counts`: document_id -> how many chunks it has now.

        Only new and content_changed documents are chunked, so only they may
        appear. A new document has no tail. Counts for documents already in
        chunks_to_delete replace that document's tail.

        With require_complete (the default), every content_changed document
        must have a count: a missing one would keep its stale tail at the
        destination for good. A document that will not be published after
        all is demoted with demote_to_skipped() first, not left out. Pass
        False only to add counts in several steps.
        """
        allowed = {entry.document_id: entry for entry in self.content_changed}
        allowed_new = {entry.document_id for entry in self.new}
        tails: list[ChunkCoordinate] = []
        for document_id, count in new_chunk_counts.items():
            _non_negative_int(f"chunk count of document {document_id}", count)
            entry = allowed.get(document_id)
            if entry is None:
                if document_id in allowed_new:
                    continue
                raise ValueError(
                    f"document {document_id} was not chunked in this diff: only "
                    "new and content_changed documents are"
                )
            # Narrowing only; see __post_init__.
            assert entry.previous is not None
            tails.extend(
                tail_coordinates(document_id, entry.previous.chunk_count, count)
            )
        if require_complete:
            missing = set(allowed) - set(new_chunk_counts)
            if missing:
                raise ValueError(
                    f"{len(missing)} content_changed document(s) have no new "
                    f"chunk count, e.g. {min(missing)}; demote them or count them"
                )
        kept = tuple(
            coordinate
            for coordinate in self.chunks_to_delete
            if coordinate.document_id not in new_chunk_counts
        )
        merged = sorted(kept + tuple(tails), key=lambda c: (c.document_id, c.ordinal))
        return replace(self, chunks_to_delete=tuple(merged))

    def __repr__(self) -> str:
        # Counts only (C11). The default repr would print every row, URL and
        # full hash the moment anyone logged a DiffResult.
        counts = ", ".join(f"{name}={count}" for name, count in self.counts().items())
        return (
            f"DiffResult(first_run={self.first_run}, "
            f"fingerprint_changed={self.fingerprint_changed}, {counts}, "
            f"chunks_to_delete={len(self.chunks_to_delete)}, "
            f"deleted_unpublished={self.deleted_unpublished})"
        )


def tail_coordinates(
    document_id: str, previous_chunk_count: int, new_chunk_count: int
) -> tuple[ChunkCoordinate, ...]:
    """The chunks a re-chunked document no longer has: ordinals from the new
    count up to the old one. Chunk ids are coordinates, so every ordinal
    below the new count is simply overwritten and needs no delete."""
    _non_negative_int("previous_chunk_count", previous_chunk_count)
    _non_negative_int("new_chunk_count", new_chunk_count)
    return tuple(
        ChunkCoordinate(document_id, ordinal)
        for ordinal in range(new_chunk_count, previous_chunk_count)
    )


# --- C8: classification -------------------------------------------------------


def classify(
    previous: Manifest | None,
    current: Sequence[DocumentRef],
    chunker_fingerprint: str,
) -> DiffResult:
    """Classify every row against the previous manifest.

    previous=None is a first run, and nothing else is. `current` is every
    row the enumeration returned for the agency, deleted and excluded rows
    included, and an empty `current` against a non-empty manifest deletes
    everything in it. Documents whose row moved must have been read and
    hashed before this is called; ones that Gate 2 lets through unread may
    keep their hashes as None.

    Raises ValueError on input no correct caller produces: a duplicate
    document, rows from two agencies, or an unread document that needed
    reading. It never substitutes a default for any of them.
    """
    if not isinstance(chunker_fingerprint, str) or not _HEX_SHA256.fullmatch(
        chunker_fingerprint
    ):
        raise ValueError("chunker_fingerprint must be 64 lowercase hex characters")

    entries: Mapping[str, ManifestDocumentEntry] = (
        {} if previous is None else previous.documents
    )
    # Every entry, not only those with a row: an absent document is deleted
    # without passing through classify_document().
    for document_id, entry in entries.items():
        _check_published(document_id, entry)
    fingerprint_changed = (
        previous is not None and previous.chunker_fingerprint != chunker_fingerprint
    )
    agency_id = None if previous is None else previous.agency_id

    seen: set[str] = set()
    changes: list[DocumentDiff | None] = []
    for ref in current:
        if ref.document_id in seen:
            raise ValueError(f"document {ref.document_id} appears twice in the rows")
        seen.add(ref.document_id)
        if agency_id is None:
            agency_id = ref.agency_id
        elif ref.agency_id != agency_id:
            raise ValueError(
                f"document {ref.document_id} belongs to a different agency than "
                "the rest of this diff"
            )
        changes.append(
            classify_document(
                entries.get(ref.document_id),
                ref,
                fingerprint_changed=fingerprint_changed,
            )
        )

    changes.extend(
        DocumentDiff(
            document_id=document_id,
            bucket=Bucket.DELETED,
            reason=DeletionReason.ABSENT_FROM_ROWS,
            current=None,
            previous=entries[document_id],
        )
        for document_id in sorted(set(entries) - seen)
    )

    return DiffResult.from_documents(
        changes,
        first_run=previous is None,
        fingerprint_changed=fingerprint_changed,
        chunker_fingerprint=chunker_fingerprint,
    )


def needs_read(
    entry: ManifestDocumentEntry | None,
    ref: DocumentRef,
    *,
    fingerprint_changed: bool,
) -> bool:
    """Whether the document must be read and hashed before classify_document
    can decide it. The caller asks this rather than reimplementing Gate 2,
    so the read decision and the classification cannot drift apart."""
    return _read_reason(entry, ref, fingerprint_changed=fingerprint_changed) is not None


def classify_document(
    entry: ManifestDocumentEntry | None,
    ref: DocumentRef,
    *,
    fingerprint_changed: bool,
) -> DocumentDiff | None:
    """One row's verdict against its previous manifest entry, so a caller
    can classify each document as it reads it. classify() is built from this
    function, so the two cannot disagree.

    Returns None for a row with a deletion signal that was never published.
    Absence from the row set cannot be seen from one row; only classify()
    reports it.
    """
    document_id = ref.document_id
    if entry is not None:
        _check_published(document_id, entry)

    def verdict(bucket: Bucket, reason: str | None = None) -> DocumentDiff:
        return DocumentDiff(document_id, bucket, reason, ref, entry)

    # Deletion signals come first: a deleted row that is also mid-scrape is
    # deleted, not held.
    signal = _deletion_signal(ref)
    if signal is not None:
        return None if entry is None else verdict(Bucket.DELETED, signal)

    skip = _skip_reason(ref)
    if skip is not None:
        return verdict(Bucket.SKIPPED, skip)

    why = _read_reason(entry, ref, fingerprint_changed=fingerprint_changed)
    if why is None and _unread(ref):
        # Gate 2 let it through unread: nothing about the row has moved.
        return verdict(Bucket.UNCHANGED)
    # Read either because it had to be or because the caller chose to; a
    # half-read row is refused either way.
    _require_hashes(ref, why or "it was partly read")

    if entry is None:
        return verdict(Bucket.NEW)
    if fingerprint_changed:
        return verdict(Bucket.CONTENT_CHANGED)
    if ref.content_sha256 != entry.content_sha256:
        return verdict(Bucket.CONTENT_CHANGED)
    # Reached only when the content matches, so the two changed buckets are
    # disjoint by construction (C13). content_origin and source_base_id are
    # fields of the published record, so moving either is a metadata change.
    if (
        ref.metadata_sha256 != entry.metadata_sha256
        or ref.content_origin != entry.content_origin
        or ref.source_base_id != entry.source_base_id
    ):
        return verdict(Bucket.METADATA_CHANGED)
    return verdict(Bucket.UNCHANGED)


def _read_reason(
    entry: ManifestDocumentEntry | None,
    ref: DocumentRef,
    *,
    fingerprint_changed: bool,
) -> str | None:
    """Why the document must be read, or None when it need not be: deleted
    and skipped rows never are, and Gate 2 spares a row that has not moved.

    Gate 2 compares everything the row itself can show without a read, not
    updated_at alone: content_origin and source_base_id are both published
    fields, so a row where either moved is read even if updated_at did not.
    """
    if _deletion_signal(ref) is not None or _skip_reason(ref) is not None:
        return None
    if entry is None:
        return "it is new"
    if fingerprint_changed:
        return "the chunker fingerprint changed"
    if (
        ref.updated_at != entry.source_updated_at
        or ref.content_origin != entry.content_origin
        or ref.source_base_id != entry.source_base_id
    ):
        return "its row moved since it was published"
    return None


def _check_published(document_id: str, entry: ManifestDocumentEntry) -> None:
    if entry.state != _PUBLISHED_STATE:
        raise ValueError(
            f"document {document_id}: manifest entry is in state {entry.state!r}, "
            f"not {_PUBLISHED_STATE!r}"
        )


def _ready(ref: DocumentRef) -> bool:
    return (
        ref.status == _READY_STATUS
        and ref.content_key is not None
        and ref.skip_reason is None
    )


def _deletion_signal(ref: DocumentRef) -> DeletionReason | None:
    if ref.is_deleted:
        return DeletionReason.IS_DELETED
    if ref.is_excluded:
        return DeletionReason.IS_EXCLUDED
    if ref.status == _NOT_FOUND_STATUS:
        return DeletionReason.NOT_FOUND
    return None


def _skip_reason(ref: DocumentRef) -> str | None:
    if ref.status != _READY_STATUS:
        return SkipReason.NOT_READY
    if ref.content_key is None:
        return SkipReason.NO_CONTENT
    if ref.skip_reason is not None:
        if not isinstance(ref.skip_reason, str) or not ref.skip_reason:
            raise ValueError(
                f"document {ref.document_id}: skip_reason must be a non-empty string"
            )
        return ref.skip_reason
    return None


def _unread(ref: DocumentRef) -> bool:
    return (
        ref.raw_sha256 is None
        and ref.content_sha256 is None
        and ref.metadata_sha256 is None
    )


def _require_hashes(ref: DocumentRef, why: str) -> None:
    if None in (ref.raw_sha256, ref.content_sha256, ref.metadata_sha256):
        raise ValueError(
            f"document {ref.document_id} has not been read and hashed, but "
            f"{why}; read it before classifying"
        )
