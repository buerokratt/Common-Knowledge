from dataclasses import dataclass, field
from datetime import datetime
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
    created_at: str = field(default_factory=lambda: str(datetime.now()))
    edited_at: str | None = None


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
