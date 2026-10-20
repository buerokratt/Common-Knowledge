"""The document source — how the publish coordinator reads CKB (Stage E's seam).

WHAT THIS IS TODAY. The interface, and the two value types that cross it.
Stage E implements it in this module: the watermark and enumeration Resql
queries (E1, E2), the read-only boto3 reads against CKB's own bucket (E7),
content precedence and readiness (E6, E12), the size guards (E9) and the three
hashes (E8). Stage F was built first, against an in-memory fake, so the
coordinator's ordering could be proved without a database or a bucket.

WHAT AN IMPLEMENTATION MUST DO, because the coordinator depends on it:

- `list_documents` returns every row the agency has, deleted and excluded
  rows included (E3). Filtering them out here makes a deletion
  indistinguishable from absence. Rows come back unread: no hashes, no text.
- `read` is called only for a row the diff says must be read (Gate 2, E5),
  one document at a time, and the coordinator drops the text before reading
  the next (E11). Nothing here may hold a corpus.
- A read-time guard (oversize, an unusable sidecar) is a skip, never an
  error: the row comes back with `skip_reason` set and no text (E9, C5).
- A failure to read is an error, never a skip: it raises StoreError, so the
  run aborts and commits nothing. A document that could not be read is not a
  document that is not ready.

This module never learns which destination is configured. If an
implementation here ever needs to branch on CONTENT_SINK, something has leaked
downward.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

from exporter.core.schemas import DocumentRef, ParsedSidecar

__all__ = ["CorpusWatermark", "DocumentSource", "ReadDocument"]


@dataclass(frozen=True)
class CorpusWatermark:
    """get_agency_export_watermark.sql's answer (E1): the newest `updated_at`
    over every row the agency has, deleted ones included, and the count of
    latest live rows. `max_updated_at` is None for an agency with no rows."""

    max_updated_at: str | None
    document_count: int

    def __post_init__(self) -> None:
        count = self.document_count
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f"document_count must be an int >= 0, got {count!r}")


@dataclass(frozen=True)
class ReadDocument:
    """One document as read: the row with its hashes, its normalised text and
    its parsed sidecar — or the row with a `skip_reason` and nothing else.

    `text` is NFC-normalised (E8), the same text `content_sha256` covers, so
    the chunker cuts exactly what was hashed.
    """

    ref: DocumentRef
    text: str | None
    sidecar: ParsedSidecar | None

    def __post_init__(self) -> None:
        document_id = self.ref.document_id
        if self.ref.skip_reason is not None:
            if self.text is not None or self.sidecar is not None:
                raise ValueError(
                    f"document {document_id}: a skipped read carries no text "
                    "and no sidecar"
                )
            return
        if self.text is None or self.sidecar is None:
            raise ValueError(
                f"document {document_id}: a read that was not skipped needs "
                "its text and its sidecar"
            )
        if None in (
            self.ref.raw_sha256,
            self.ref.content_sha256,
            self.ref.metadata_sha256,
        ):
            raise ValueError(f"document {document_id}: a read document is hashed")
        if self.ref.metadata_sha256 != self.sidecar.metadata_sha256:
            raise ValueError(
                f"document {document_id}: metadata_sha256 does not match the "
                "sidecar it was read with"
            )

    def __repr__(self) -> str:
        # Never the text (F13): a ReadDocument in a log line or a traceback
        # would otherwise carry Estonian government content.
        state = "skipped" if self.ref.skip_reason is not None else "read"
        return f"ReadDocument(document_id={self.ref.document_id!r}, {state})"


class DocumentSource(ABC):
    """CKB, as the publish coordinator sees it. See the module docstring for
    the contract every implementation meets."""

    @abstractmethod
    def watermark(self, agency_id: str) -> CorpusWatermark:
        """The corpus watermark (E1). Raises StoreError on failure."""

    @abstractmethod
    def list_documents(self, agency_id: str) -> Sequence[DocumentRef]:
        """Every row for the agency, unread, deleted and excluded rows
        included (E2, E3). Raises StoreError on failure."""

    @abstractmethod
    def read(self, ref: DocumentRef) -> ReadDocument:
        """Read and hash one document (E7, E8), or return it skipped by a
        read-time guard (E9). Raises StoreError on failure."""
