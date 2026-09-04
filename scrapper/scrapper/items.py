from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Optional


@dataclass
class Metadata:
    cleaned: bool = False
    edited: bool = False


@dataclass
class FileItem:
    body: bytes
    source_url: str
    extension: str


@dataclass
class MetadataItem:
    file_type: str
    source_url: str
    metadata: Metadata
    page_title: str
    external_id: Optional[str] = ""
    version: str = "1.0"
    # Must be UTC, not naive local time -- this value is sent to Postgres as
    # `last_scraped_at` (TIMESTAMP WITH TIME ZONE). A naive local timestamp
    # gets misinterpreted as already-UTC, silently shifting it by the host's
    # UTC offset -- which can permanently satisfy claim queries like
    # `last_scraped_at < :reference_time` (itself always computed in UTC),
    # causing a file to be re-claimed for scraping indefinitely.
    created_at: str = field(default_factory=lambda: str(datetime.now(UTC)))
    edited_at: str | None = None
    language: Optional[str] = None


@dataclass
class ScrappedItem:
    file: FileItem
    metadata: MetadataItem
    hash: str
    file_path: str | None = None
    file_path_uploaded: str | None = None
    metadata_path: str | None = None
    metadata_path_uploaded: str | None = None
    path: str | None = None
    source_file_id: str | None = None
