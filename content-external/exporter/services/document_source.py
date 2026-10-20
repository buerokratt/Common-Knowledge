"""Enumeration reads Resql directly under the named read-only SELECT exception.

Like agency_guard, this bypasses Ruuter only for query-named reads. Every row
must reach the diff: omitting an unfinished or malformed row would turn it
into a downstream deletion. Malformed responses therefore fail the whole run.
URLs and timestamps stay verbatim so enumeration cannot change their meaning.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

import requests

from exporter.core.schemas import AgencyWatermark, DocumentRef, ReadDocument

if TYPE_CHECKING:
    from exporter.api.config import Settings


RESQL_TIMEOUT_SECONDS = 30.0

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


class CkbDocumentSource(DocumentSource):
    def __init__(self, settings: "Settings") -> None:
        self._settings = settings

    def _rows(self, query: str, agency_id: str) -> list[Any]:
        url = f"{self._settings.ckb_resql}/{query}"
        try:
            response = requests.get(
                url,
                params={"agency_base_id": agency_id},
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
        rows = self._rows("source_file/list_agency_export_documents", agency_id)
        documents: list[DocumentRef] = []
        seen: set[str] = set()
        for raw in rows:
            row = _validate_row(raw, _DOCUMENT_COLUMNS)
            document_id = row["baseId"]
            if document_id in seen:
                raise DocumentSourceError(f"duplicate document {document_id}")
            seen.add(document_id)
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
                    metadata_key=row["editedMetadataUrl"] or row["cleanedMetadataUrl"],
                    content_origin=origin,
                    file_size=row["fileSize"],
                )
            )
        return documents

    def read(self, ref: DocumentRef) -> ReadDocument:
        raise NotImplementedError
