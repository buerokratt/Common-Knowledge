from pydantic import BaseModel
from typing import Optional, List, Dict
from enum import Enum
from datetime import datetime


class TaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

class FileUploadRequest(BaseModel):
    source_file_path: str

class FileUploadResponse(BaseModel):
    task_id: str
    status: str = "pending"

class FileContentUploadRequest(BaseModel):
    file_content: str  # Base64 encoded file content
    file_path: str  # S3 destination path
    content_type: Optional[str] = "application/octet-stream"

class FileContentUploadResponse(BaseModel):
    blob_storage_path: str
    status: str = "completed"
    file_size: int

class DownloadFileRequest(BaseModel):
    paths: List[str]  

class DownloadUrlItem(BaseModel):
    path: str
    download_url: str
    expires_at: datetime
    error_message: Optional[str] = None

class DownloadFileResponse(BaseModel):
    download_urls: List[DownloadUrlItem]

class UploadTaskStatusResponse(BaseModel):
    task_id: str
    status: TaskStatus
    source_file_path: str
    blob_storage_path: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

class FileUploadInfo(BaseModel):
    path: str
    content_type: str = "application/octet-stream"

class UploadUrlRequest(BaseModel):
    files: List[FileUploadInfo]
    expires_in: Optional[int] = None

class UploadUrlItem(BaseModel):
    path: str
    upload_url: str
    expires_at: datetime

class UploadUrlResponse(BaseModel):
    upload_urls: List[UploadUrlItem]

class FileDownloadItem(BaseModel):
    s3_path: str
    local_path: str
    is_folder: bool = False  # New field - explicitly specify if it's a folder

class CallbackRequest(BaseModel):
    url: str
    method: str = "POST"
    headers: Optional[Dict[str, str]] = None
    body: Optional[Dict] = None

class DownloadToVolumeRequest(BaseModel):
    files: List[FileDownloadItem]
    callback: Optional[CallbackRequest] = None

class FileDownloadResult(BaseModel):
    s3_path: str
    local_path: str
    status: str  # "success" or "failed"
    file_size: Optional[int] = None
    error_message: Optional[str] = None
    is_folder: bool = False  # Track what type was downloaded
    files_count: Optional[int] = None  # For folders, number of files downloaded

class DownloadToVolumeResponse(BaseModel):
    total_files: int
    successful_downloads: int
    failed_downloads: int
    results: List[FileDownloadResult]

class DownloadTaskResponse(BaseModel):
    task_id: str
    status: TaskStatus
    total_files: int
    successful_downloads: int = 0
    failed_downloads: int = 0
    results: List[FileDownloadResult] = []

class DownloadTaskStatusResponse(BaseModel):
    task_id: str
    status: TaskStatus
    total_files: int
    completed_files: int
    failed_files: int
    results: List[FileDownloadResult]
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

# Schemas for delete functionality from local volume (existing)
class FileDeleteItem(BaseModel):
    local_path: str

class DeleteFromVolumeRequest(BaseModel):
    files: List[FileDeleteItem]

class FileDeleteResult(BaseModel):
    local_path: str
    status: str  # "success" or "failed"
    error_message: Optional[str] = None

class DeleteFromVolumeResponse(BaseModel):
    total_files: int
    successful_deletions: int
    failed_deletions: int
    results: List[FileDeleteResult]

# Schemas for zip and upload functionality
class FolderZipItem(BaseModel):
    s3_path: str  # Source folder path in S3
    s3_zip_path: str  # Destination zip file path in S3
    excluded_folders: Optional[List[str]] = []  # List of subfolder names to exclude from this folder's zip

class ZipAndUploadRequest(BaseModel):
    folders: List[FolderZipItem]
    callback: Optional[CallbackRequest] = None

class FolderZipResult(BaseModel):
    s3_path: str
    s3_zip_path: str
    status: str  # "success" or "failed"
    zip_size: Optional[int] = None
    files_count: Optional[int] = None  # Total count of files and folders in the zip
    excluded_subfolders: Optional[List[str]] = []  # List of subfolders that were excluded
    error_message: Optional[str] = None
    data_hash: str = None


class ZipAndUploadResponse(BaseModel):
    total_folders: int
    successful_zips: int
    failed_zips: int
    results: List[FolderZipResult]

class ZipTaskResponse(BaseModel):
    task_id: str
    status: TaskStatus
    total_folders: int
    successful_zips: int = 0
    failed_zips: int = 0
    results: List[FolderZipResult] = []

class ZipTaskStatusResponse(BaseModel):
    task_id: str
    status: TaskStatus
    total_folders: int
    completed_folders: int
    failed_folders: int
    results: List[FolderZipResult]
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

# Schemas for move functionality
class FileMoveItem(BaseModel):
    s3_from_path: str  # Source file/folder path in S3
    s3_to_path: str    # Destination file/folder path in S3
    is_folder: bool = False  # Specify if it's a folder

class MoveFilesRequest(BaseModel):
    files: List[FileMoveItem]
    callback: Optional[CallbackRequest] = None

class FileMoveResult(BaseModel):
    s3_from_path: str
    s3_to_path: str
    status: str  # "success" or "failed"
    error_message: Optional[str] = None

class MoveFilesResponse(BaseModel):
    total_files: int
    successful_moves: int
    failed_moves: int
    results: List[FileMoveResult]

class MoveTaskResponse(BaseModel):
    task_id: str
    status: TaskStatus
    total_files: int
    successful_moves: int = 0
    failed_moves: int = 0
    results: List[FileMoveResult] = []

class MoveTaskStatusResponse(BaseModel):
    task_id: str
    status: TaskStatus
    total_files: int
    completed_files: int
    failed_files: int
    results: List[FileMoveResult]
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

# NEW: Schemas for delete functionality from blob storage
class BlobFileDeleteItem(BaseModel):
    s3_path: str  # File/folder path in S3 to delete
    is_folder: bool = False  # Specify if it's a folder

class DeleteFilesRequest(BaseModel):
    files: List[BlobFileDeleteItem]
    callback: Optional[CallbackRequest] = None

class BlobFileDeleteResult(BaseModel):
    s3_path: str
    status: str  # "success" or "failed"
    error_message: Optional[str] = None

class DeleteFilesResponse(BaseModel):
    total_files: int
    successful_deletions: int
    failed_deletions: int
    results: List[BlobFileDeleteResult]

class DeleteTaskResponse(BaseModel):
    task_id: str
    status: TaskStatus
    total_files: int
    successful_deletions: int = 0
    failed_deletions: int = 0
    results: List[BlobFileDeleteResult] = []

class DeleteTaskStatusResponse(BaseModel):
    task_id: str
    status: TaskStatus
    total_files: int
    completed_files: int
    failed_files: int
    results: List[BlobFileDeleteResult]
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime