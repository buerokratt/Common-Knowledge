from pydantic import BaseModel, FilePath, DirectoryPath


class EntityToClean(BaseModel):
    file_path: FilePath
    meta_data_path: FilePath
    directory_path: DirectoryPath
    source_file_id: str
    url: str
    logs_path: FilePath
    source_base_id: str
    agency_base_id: str
    source_run_report_base_id: str

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
    logs_path: FilePath
    files: list[SourceCleaningFile]
    use_llm: bool = False
    use_llm_correction: bool = False