"""Enumeration reads Resql directly under the named read-only SELECT exception.

Like agency_guard, this bypasses Ruuter only for query-named reads. Every row
must reach the diff: omitting an unfinished or malformed row would turn it
into a downstream deletion. Malformed responses therefore fail the whole run.
URLs and timestamps stay verbatim so enumeration cannot change their meaning.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import requests

from exporter.core.metadata import MAX_SIDECAR_BYTES, SidecarRejection, parse_sidecar
from exporter.core.schemas import AgencyWatermark, DocumentRef, ReadDocument
from exporter.core.text_normaliser import normalise_nfc, sha256_hex, sha256_text
from exporter.services.ckb_bucket import (
    CkbBucket,
    InvalidObjectKeyError,
    ObjectTooLargeError,
)

if TYPE_CHECKING:
    from exporter.api.config import Settings


logger = logging.getLogger(__name__)

RESQL_TIMEOUT_SECONDS = 30.0
NIL_UUID = "00000000-0000-0000-0000-000000000000"

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
    def __init__(self, settings: "Settings") -> None:
        self._settings = settings
        self._bucket = CkbBucket(settings)

    def _rows(self, query: str, agency_id: str, **params: str | int) -> list[Any]:
        url = f"{self._settings.ckb_resql}/{query}"
        try:
            response = requests.get(
                url,
                params={"agency_base_id": agency_id, **params},
                timeout=RESQL_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
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
                        is_deleted=row["isDeleted"],
                        updated_at=row["updatedAt"],
                        content_key=row["editedDataUrl"] or row["cleanedDataUrl"],
                        metadata_key=row["editedMetadataUrl"]
                        or row["cleanedMetadataUrl"],
                        content_origin=origin,
                        file_size=row["fileSize"],
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
