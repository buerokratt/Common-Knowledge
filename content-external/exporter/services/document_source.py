"""Enumeration reads Resql directly under the named read-only SELECT exception.

Like agency_guard, this bypasses Ruuter only for query-named reads. Every row
must reach the diff: omitting an unfinished or malformed row would turn it
into a downstream deletion. Malformed responses therefore fail the whole run.
URLs and timestamps stay verbatim so enumeration cannot change their meaning.

A deleted source deletes its files. CKB marks only the source row
(source/delete_source.sql) and never its files, so a file whose source's
latest version is deleted is enumerated as deleted.

Uploaded files are skipped at enumeration, never read. Their sidecar and row
URL are presigned links, so they would only ever be skipped as uncitable
(core/metadata.py). Skipping them unread costs no S3 read on any run, and
because the verdict comes from the row rather than from configuration, it
does not withhold the corpus watermark the way a read-time skip does.

A read fetches the sidecar before the content, so a rejected sidecar costs one
small GET instead of a full content download on every run.

Each Resql call is retried under F8's rule (services/backoff.py): connection
errors, 408/429 and 5xx, with full jitter and Retry-After honoured. A read
timeout is the one carve-out. Resql does not cancel a query its client gave up
on, so a retry would run alongside the abandoned one and stack load
(RUNBOOK.md, "Do not retry immediately after a timeout"). That holds whether
the timeout hits before the response or while its body streams in, which
requests reports as a ConnectionError. A connect timeout never reached Resql
and is retried.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import requests
from urllib3.exceptions import ReadTimeoutError

from exporter.core.metadata import MAX_SIDECAR_BYTES, SidecarRejection, parse_sidecar
from exporter.core.schemas import AgencyWatermark, DocumentRef, ReadDocument
from exporter.core.text_normaliser import normalise_nfc, sha256_hex, sha256_text
from exporter.services.backoff import (
    DEFAULT_POLICY,
    BackoffPolicy,
    is_retryable_http,
    retry_with_backoff,
)
from exporter.services.ckb_bucket import (
    CkbBucket,
    InvalidObjectKeyError,
    ObjectTooLargeError,
)

if TYPE_CHECKING:
    from exporter.api.config import Settings


logger = logging.getLogger(__name__)

NIL_UUID = "00000000-0000-0000-0000-000000000000"

UPLOADED_FILE_TYPE = "uploaded_file"
# The skip reason for an uploaded file. Distinct from the read-time
# uncitable_source_url: this one is decided from the row, with nothing read.
UPLOADED_FILE_UNCITABLE = "uploaded_file_uncitable"

_DOCUMENT_COLUMNS = {
    "baseId": (str,),
    "sourceBaseId": (str,),
    "url": (str, type(None)),
    "pageTitle": (str, type(None)),
    "subsector": (str, type(None)),
    "status": (str,),
    "cleanedDataUrl": (str, type(None)),
    "cleanedMetadataUrl": (str, type(None)),
    "editedDataUrl": (str, type(None)),
    "editedMetadataUrl": (str, type(None)),
    "fileSize": (int, type(None)),
    "isExcluded": (bool,),
    "isDeleted": (bool,),
    "type": (str,),
    "sourceIsDeleted": (bool,),
    "updatedAt": (str,),
}
_WATERMARK_COLUMNS = {
    "maxUpdatedAt": (str, type(None)),
    "liveCount": (int,),
}


class DocumentSourceError(RuntimeError):
    """Enumeration could not be trusted; the run must fail."""


class DocumentSource(ABC):
    @abstractmethod
    def watermark(self, agency_id: str) -> AgencyWatermark:
        raise NotImplementedError

    @abstractmethod
    def list_documents(self, agency_id: str) -> list[DocumentRef]:
        raise NotImplementedError

    @abstractmethod
    def read(self, ref: DocumentRef) -> ReadDocument:
        raise NotImplementedError


def _read_timed_out(exc: BaseException) -> bool:
    """A read timeout, in either form requests raises it: ReadTimeout while
    waiting for the response, or ConnectionError wrapping urllib3's
    ReadTimeoutError once the body has started (requests/models.py)."""
    if isinstance(exc, requests.ReadTimeout):
        return True
    return isinstance(exc, requests.ConnectionError) and any(
        isinstance(arg, ReadTimeoutError) for arg in exc.args
    )


def is_retryable_resql(exc: BaseException) -> bool:
    """F8's rule, less a read timeout: see the module docstring."""
    return not _read_timed_out(exc) and is_retryable_http(exc)


def _validate_row(row: object, columns: dict[str, tuple[type, ...]]) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise DocumentSourceError("Resql returned a non-object row")
    for name, types in columns.items():
        if name not in row or type(row[name]) not in types:
            # Exact types prevent JSON booleans being accepted as integer counts.
            raise DocumentSourceError(f"Resql column {name} is missing or mistyped")
    return row


def _skipped(ref: DocumentRef, reason: str) -> ReadDocument:
    return ReadDocument(
        replace(
            ref,
            raw_sha256=None,
            content_sha256=None,
            metadata_sha256=None,
            skip_reason=reason,
        ),
        text=None,
        sidecar=None,
    )


class CkbDocumentSource(DocumentSource):
    def __init__(
        self, settings: "Settings", *, backoff: BackoffPolicy = DEFAULT_POLICY
    ) -> None:
        self._settings = settings
        self._bucket = CkbBucket(settings)
        self._backoff = backoff

    def _rows(self, query: str, agency_id: str, **params: str | int) -> list[Any]:
        url = f"{self._settings.ckb_resql}/{query}"

        def get() -> requests.Response:
            response = requests.get(
                url,
                params={"agency_base_id": agency_id, **params},
                timeout=self._settings.ckb_resql_timeout,
            )
            response.raise_for_status()
            return response

        try:
            response = retry_with_backoff(
                get,
                describe=f"Resql {query}",
                policy=self._backoff,
                is_retryable=is_retryable_resql,
            )
        except requests.RequestException as exc:
            raise DocumentSourceError("could not read Resql") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise DocumentSourceError("Resql returned a non-JSON body") from exc
        if isinstance(payload, dict):
            payload = payload.get("response", payload)
        if not isinstance(payload, list):
            raise DocumentSourceError("Resql did not return a list")
        return payload

    def watermark(self, agency_id: str) -> AgencyWatermark:
        rows = self._rows("agency/get_agency_export_watermark", agency_id)
        if len(rows) != 1:
            raise DocumentSourceError("Resql did not return exactly one watermark")
        row = _validate_row(rows[0], _WATERMARK_COLUMNS)
        if row["liveCount"] < 0:
            raise DocumentSourceError("Resql liveCount is negative")
        return AgencyWatermark(row["maxUpdatedAt"], row["liveCount"])

    def list_documents(self, agency_id: str) -> list[DocumentRef]:
        """Pages are separate snapshots; the base_id cursor returns each ID at most once.
        This relies on F reading watermark() before listing: writes during listing
        have later updated_at values and are picked up by the next run.
        """
        documents: list[DocumentRef] = []
        after_base_id = NIL_UUID
        page_size = self._settings.export_page_size
        pages = 0
        while True:
            rows = self._rows(
                "source_file/list_agency_export_documents",
                agency_id,
                after_base_id=after_base_id,
                page_size=page_size,
            )
            pages += 1
            for raw in rows:
                row = _validate_row(raw, _DOCUMENT_COLUMNS)
                document_id = row["baseId"]
                if document_id <= after_base_id:
                    raise DocumentSourceError("Resql baseId did not advance the cursor")
                after_base_id = document_id
                origin = (
                    "edited"
                    if row["editedDataUrl"]
                    else "cleaned"
                    if row["cleanedDataUrl"]
                    else None
                )
                documents.append(
                    DocumentRef(
                        document_id=document_id,
                        source_base_id=row["sourceBaseId"],
                        agency_id=agency_id,
                        url=row["url"],
                        page_title=row["pageTitle"],
                        subsector=row["subsector"],
                        status=row["status"],
                        is_excluded=row["isExcluded"],
                        is_deleted=row["isDeleted"] or row["sourceIsDeleted"],
                        updated_at=row["updatedAt"],
                        content_key=row["editedDataUrl"] or row["cleanedDataUrl"],
                        metadata_key=row["editedMetadataUrl"]
                        or row["cleanedMetadataUrl"],
                        content_origin=origin,
                        file_size=row["fileSize"],
                        skip_reason=(
                            UPLOADED_FILE_UNCITABLE
                            if row["type"] == UPLOADED_FILE_TYPE
                            else None
                        ),
                    )
                )
            if len(rows) < page_size:
                break
        logger.info(
            "document enumeration agency=%s pages=%d rows=%d",
            agency_id,
            pages,
            len(documents),
        )
        return documents

    def read(self, ref: DocumentRef) -> ReadDocument:
        # The sidecar first: it is small, and a rejected one makes the
        # content download pointless.
        try:
            metadata = (
                None
                if ref.metadata_key is None
                else self._bucket.get_bytes(ref.metadata_key, MAX_SIDECAR_BYTES)
            )
        except InvalidObjectKeyError:
            return _skipped(ref, "metadata_key_invalid")
        except ObjectTooLargeError:
            return _skipped(ref, SidecarRejection.TOO_LARGE.value)
        sidecar = parse_sidecar(
            metadata,
            row_url=ref.url,
            require_sidecar=self._settings.require_metadata_sidecar,
        )
        if isinstance(sidecar, SidecarRejection):
            return _skipped(ref, sidecar.value)

        try:
            raw = self._bucket.get_bytes(
                ref.content_key, self._settings.max_document_bytes
            )
        except InvalidObjectKeyError:
            return _skipped(ref, "content_key_invalid")
        except ObjectTooLargeError:
            return _skipped(ref, "content_too_large")
        if raw is None:
            return _skipped(ref, "content_missing")
        try:
            text = normalise_nfc(raw.decode("utf-8"))
        except UnicodeDecodeError:
            return _skipped(ref, "content_undecodable")
        return ReadDocument(
            replace(
                ref,
                raw_sha256=sha256_hex(raw),
                content_sha256=sha256_text(text),
                metadata_sha256=sidecar.metadata_sha256,
                skip_reason=None,
            ),
            text=text,
            sidecar=sidecar,
        )
