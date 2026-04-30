from pathlib import Path

from pydantic import BaseModel, ConfigDict, DirectoryPath, Field, FilePath, field_validator

# Restrict all paths to this root to prevent directory traversal
_ALLOWED_ROOT = Path("/scrapped-data")


def _assert_within_root(p: Path) -> Path:
    try:
        p.resolve().relative_to(_ALLOWED_ROOT.resolve())
    except ValueError as e:
        raise ValueError(
            f"Path {p} is outside the allowed root {_ALLOWED_ROOT}"
        ) from e
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
    def validate_file_within_root(cls, v: object) -> Path:
        return _assert_within_root(Path(v))  # type: ignore[arg-type]

    @field_validator("directory_path", mode="before")
    @classmethod
    def validate_dir_within_root(cls, v: object) -> Path:
        return _assert_within_root(Path(v))  # type: ignore[arg-type]


class SourceCleaningFile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    base_id: str = Field(alias="baseId")
    source_base_id: str = Field(alias="sourceBaseId")
    url: str
    original_data_url: str | None = Field(default=None, alias="originalDataUrl")
    original_metadata_url: str | None = Field(default=None, alias="originalMetadataUrl")


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
    def validate_logs_path_within_root(cls, v: object) -> Path:
        return _assert_within_root(Path(v))  # type: ignore[arg-type]
