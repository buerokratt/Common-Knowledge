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