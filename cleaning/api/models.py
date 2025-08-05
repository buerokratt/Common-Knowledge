from pydantic import BaseModel, FilePath, DirectoryPath


class EntityToClean(BaseModel):
    file_path: FilePath
    meta_data_path: FilePath
    directory_path: DirectoryPath
    source_file_id: str
    logs_path: FilePath