from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional, Tuple, Callable


class BlobStorageException(Exception):
    pass


class BlobStorageProvider(ABC):
    @abstractmethod
    def upload_file(self, source_file_path: str, destination_path: str) -> str:
        pass

    @abstractmethod
    def upload_file_content(self, file_content: bytes, destination_path: str, content_type: str = "application/octet-stream") -> str:
        """Upload file content directly to blob storage.
        
        Args:
            file_content: Raw file content as bytes
            destination_path: Blob storage destination path
            content_type: MIME type of the file
            
        Returns:
            str: Blob storage URI of uploaded file
        """
        pass

    @abstractmethod
    def download_file(self, blob_path: str, local_file_path: str) -> bool:
        """Download a file from blob storage to local filesystem.
        
        Args:
            blob_path: Path of the file in blob storage
            local_file_path: Local path where the file should be saved
            
        Returns:
            bool: True if download was successful, False otherwise
        """
        pass

    @abstractmethod
    def download_folder(self, s3_prefix: str, local_folder_path: str, 
                       exclusion_filter: Optional[Callable[[str], bool]] = None) -> Tuple[int, int, List[Tuple[str, str, bool, Optional[str]]], List[str]]:
        """Download all files from a blob storage folder to local filesystem.
        
        Args:
            s3_prefix: Blob storage prefix/folder path
            local_folder_path: Local folder where files should be saved
            exclusion_filter: Optional function that takes relative path and returns True if file should be excluded
            
        Returns:
            Tuple of (successful_count, failed_count, results, excluded_paths)
            Results is list of (blob_key, local_path, success, error_message)
            Excluded_paths is list of relative paths that were excluded
        """
        pass

    @abstractmethod
    def list_folder_files(self, s3_prefix: str) -> List[Tuple[str, int]]:
        """List all files in a blob storage folder/prefix.
        
        Args:
            s3_prefix: Blob storage prefix/folder path
            
        Returns:
            List of tuples containing (file_key, file_size)
        """
        pass

    @abstractmethod
    def generate_download_url(self, path: str) -> tuple[str, datetime]:
        pass

    @abstractmethod
    def file_exists(self, path: str) -> bool:
        pass

    @abstractmethod
    def generate_upload_urls(self, paths: List[str], content_type: Optional[str] = None, expires_in: Optional[int] = None) -> List[tuple[str, str, datetime]]:
        """Generate presigned upload URLs for multiple blob paths.
        
        Args:
            paths: List of blob paths to generate upload URLs for
            content_type: Optional content type for the upload
            expires_in: Optional expiration time in seconds
            
        Returns:
            List of tuples containing (path, upload_url, expires_at)
        """
        pass

    @abstractmethod
    def move_file(self, source_path: str, destination_path: str) -> bool:
        """Move a file from source to destination within blob storage.
        
        Args:
            source_path: Source file path in blob storage
            destination_path: Destination file path in blob storage
            
        Returns:
            bool: True if move was successful, False otherwise
        """
        pass

    @abstractmethod
    def move_folder(self, source_prefix: str, destination_prefix: str) -> bool:
        """Move a folder from source to destination within blob storage.
        
        Args:
            source_prefix: Source folder prefix in blob storage (should end with /)
            destination_prefix: Destination folder prefix in blob storage (should end with /)
            
        Returns:
            bool: True if move was successful, False otherwise
        """
        pass
    
    @abstractmethod
    def delete_file(self, path: str) -> bool:
        """Delete a file from blob storage.
        
        Args:
            path: File path in blob storage to delete
            
        Returns:
            bool: True if deletion was successful, False otherwise
        """
        pass

    @abstractmethod
    def delete_folder(self, prefix: str) -> bool:
        """Delete a folder and all its contents from blob storage.
        
        Args:
            prefix: Folder prefix in blob storage (should end with /)
            
        Returns:
            bool: True if deletion was successful, False otherwise
        """
        pass

    @abstractmethod
    def clean_path(self, path: str) -> str:
        pass


def get_blob_storage_provider(provider_name: str) -> BlobStorageProvider:
    if provider_name == "s3":
        from app.services.s3_provider import s3_provider
        return s3_provider
    else:
        raise BlobStorageException(f"Invalid provider name: {provider_name}")


storage_provider = get_blob_storage_provider('s3')