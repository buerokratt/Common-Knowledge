from pathlib import Path
from pydantic import BaseModel, FilePath, DirectoryPath, field_validator

# Restrict all paths to this root to prevent directory traversal
_ALLOWED_ROOT = Path("/scrapped-data")


def _assert_within_root(p: Path) -> Path:
    try:
        p.resolve().relative_to(_ALLOWED_ROOT.resolve())
    except ValueError:
        raise ValueError(f"Path {p} is outside the allowed root {_ALLOWED_ROOT}")
    return p


class EntityToClean(BaseModel):
    file_path: FilePath
    meta_data_path: FilePath
    directory_path: DirectoryPath
    source_file_id: str
    source_base_id: str
    agency_base_id: str
    source_run_report_base_id: str
    url: str
    logs_path: FilePath
    use_llm: bool = False
    use_llm_correction: bool = False
    extract_images: bool = False

    @field_validator("file_path", "meta_data_path", "logs_path", mode="before")
    @classmethod
    def validate_file_within_root(cls, v):
        return _assert_within_root(Path(v))

    @field_validator("directory_path", mode="before")
    @classmethod
    def validate_dir_within_root(cls, v):
        return _assert_within_root(Path(v))


class SourceCleaningFile(BaseModel):
    baseId: str
    sourceBaseId: str
    url: str
    originalDataUrl: str | None = None
    originalMetadataUrl: str | None = None


class SourceCleaningTask(BaseModel):
    source_base_id: str
    agency_base_id: str
    source_run_report_base_id: str
    scraping_log_url: str = ""
    logs_path: Path
    files: list[SourceCleaningFile]
    use_llm: bool = False
    use_llm_correction: bool = False
    extract_images: bool = False

    @field_validator("logs_path", mode="before")
    @classmethod
    def validate_logs_path_within_root(cls, v):
        return _assert_within_root(Path(v))
