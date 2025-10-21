import os
import logging
import uuid
import tempfile
import shutil
import requests
import hashlib
from typing import List, Dict, Optional
from datetime import datetime
from app.schemas import (
    ZipAndUploadRequest,
    ZipAndUploadResponse,
    FolderZipItem,
    FolderZipResult,
    ZipTaskResponse,
    TaskStatus
)
from app.services.blob_storage import storage_provider, BlobStorageException

logger = logging.getLogger(__name__)

# In-memory task store for zip tasks
_zip_tasks: Dict[str, dict] = {}


def should_exclude_subfolder(relative_path: str, excluded_folders: List[str]) -> bool:
    """Check if a subfolder should be excluded from the zip based on its relative path."""
    if not excluded_folders:
        return False
    
    # Normalize the relative path
    normalized_path = relative_path.replace('\\', '/').strip('/')
    
    for excluded in excluded_folders:
        normalized_excluded = excluded.replace('\\', '/').strip('/')
        
        # Check if the path starts with the excluded folder name
        if (normalized_path == normalized_excluded or 
            normalized_path.startswith(normalized_excluded + '/')):
            return True
    
    return False


def create_zip_task(folders: List[FolderZipItem], callback: Optional = None) -> str:
    """Create a new zip task in memory."""
    task_id = str(uuid.uuid4())
    
    _zip_tasks[task_id] = {
        "task_id": task_id,
        "status": TaskStatus.PENDING,
        "folders": folders,
        "callback": callback,
        "total_folders": len(folders),
        "completed_folders": 0,
        "failed_folders": 0,
        "results": [],
        "error_message": None,
        "created_at": datetime.now(),
        "updated_at": datetime.now()
    }
    
    return task_id


def get_zip_task(task_id: str) -> Optional[dict]:
    """Get zip task status by task ID."""
    return _zip_tasks.get(task_id)


def update_zip_task(task_id: str, **updates) -> None:
    """Update zip task status and related fields."""
    if task_id in _zip_tasks:
        for key, value in updates.items():
            _zip_tasks[task_id][key] = value
        _zip_tasks[task_id]["updated_at"] = datetime.now()


def compute_file_hash(file_path):
    hash_func = hashlib.sha1()

    with open(file_path, 'rb') as file:
        # Read the file in chunks of 8192 bytes
        while chunk := file.read(8192):
            hash_func.update(chunk)

    return hash_func.hexdigest()


def process_single_folder_zip(folder_item: FolderZipItem) -> FolderZipResult:
    """Process zipping of a single folder, excluding specified subfolders."""
    
    temp_dir = None
    
    try:
        # Clean the s3_path - remove s3:// prefix if present
        clean_s3_path = folder_item.s3_path
        if clean_s3_path.startswith('s3://'):
            parts = clean_s3_path.replace('s3://', '').split('/', 1)
            if len(parts) > 1:
                clean_s3_path = parts[1]
            else:
                clean_s3_path = parts[0]
        
        # Clean the s3_zip_path
        clean_s3_zip_path = folder_item.s3_zip_path
        if clean_s3_zip_path.startswith('s3://'):
            parts = clean_s3_zip_path.replace('s3://', '').split('/', 1)
            if len(parts) > 1:
                clean_s3_zip_path = parts[1]
            else:
                clean_s3_zip_path = parts[0]
        
        # Create temporary directory for downloads
        temp_dir = tempfile.mkdtemp(prefix=f"zip_folder_{uuid.uuid4().hex[:8]}_")
        local_folder_path = os.path.join(temp_dir, "folder_content")
        
        logger.info(f"Downloading folder {clean_s3_path} to temporary location {local_folder_path}")
        
        # Create exclusion filter function
        def exclusion_filter(relative_path: str) -> bool:
            return should_exclude_subfolder(relative_path, folder_item.excluded_folders or [])
        
        # Download the folder with exclusion filter
        successful_count, failed_count, download_results, excluded_paths = storage_provider.download_folder(
            clean_s3_path, local_folder_path, exclusion_filter
        )

        # Extract unique excluded subfolders from excluded paths
        excluded_subfolders_found = []
        for excluded_path in excluded_paths:
            excluded_subfolder = excluded_path.split('/')[0] if '/' in excluded_path else excluded_path
            if excluded_subfolder not in excluded_subfolders_found:
                excluded_subfolders_found.append(excluded_subfolder)
        
        # If no files were downloaded, create an empty folder in the zip
        if successful_count == 0:
            logger.info(f"No files found in {clean_s3_path}, creating zip with empty folder")
        
        # Create zip file in temp directory
        base_name = os.path.join(temp_dir, "folder_content")
    
        
        shutil.make_archive(base_name, 'zip', local_folder_path)
        temp_zip_path = base_name + ".zip"

        logger.info(f"Creating zip file {temp_zip_path} with {successful_count} files")
        
        # Get zip file size
        zip_size = os.path.getsize(temp_zip_path)

        hashed = compute_file_hash(temp_zip_path)
        
        logger.info(f"Zip file created successfully. Size: {zip_size} bytes")
        
        # Upload zip file to S3
        blob_storage_path = storage_provider.upload_file(temp_zip_path, clean_s3_zip_path)
        
        logger.info(f"Zip file uploaded to {blob_storage_path}")
        
        return FolderZipResult(
            s3_path=folder_item.s3_path,
            s3_zip_path=folder_item.s3_zip_path,
            status="success",
            zip_size=zip_size,
            files_count=max(successful_count, 1),  # At least 1 for the empty folder case
            excluded_subfolders=excluded_subfolders_found,
            data_hash=hashed,
        )
        
    except BlobStorageException as e:
        error_msg = f"Blob storage error: {str(e)}"
        return FolderZipResult(
            s3_path=folder_item.s3_path,
            s3_zip_path=folder_item.s3_zip_path,
            status="failed",
            error_message=error_msg,
            files_count=0,
            excluded_subfolders=[]
        )
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        return FolderZipResult(
            s3_path=folder_item.s3_path,
            s3_zip_path=folder_item.s3_zip_path,
            status="failed",
            error_message=error_msg,
            files_count=0,
            excluded_subfolders=[]
        )
    finally:
        # Cleanup temporary directory
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
                logger.info(f"Cleaned up temporary directory {temp_dir}")
            except Exception as e:
                logger.warning(f"Failed to cleanup temporary directory {temp_dir}: {str(e)}")


def process_zip_task(task_id: str) -> None:
    """Process a zip task by zipping all folders."""
    task_data = _zip_tasks.get(task_id)
    if not task_data:
        logger.error(f"Zip task {task_id} not found")
        return

    try:
        update_zip_task(task_id, status=TaskStatus.PROCESSING)
        
        results: List[FolderZipResult] = []
        successful_zips = 0
        failed_zips = 0
        
        for folder_item in task_data["folders"]:
            try:
                result = process_single_folder_zip(folder_item)
                results.append(result.model_dump(mode='json'))
                
                if result.status == "success":
                    successful_zips += 1
                    logger.info(f"Successfully zipped folder {folder_item.s3_path}")
                else:
                    failed_zips += 1
                    logger.error(f"Failed to zip folder {folder_item.s3_path}: {result.error_message}")
                    
            except Exception as e:
                error_msg = f"Unexpected error processing {folder_item.s3_path}: {str(e)}"
                results.append(FolderZipResult(
                    s3_path=folder_item.s3_path,
                    s3_zip_path=folder_item.s3_zip_path,
                    status="failed",
                    error_message=error_msg,
                    files_count=0,
                    excluded_subfolders=[]
                ).model_dump(mode='json'))
                failed_zips += 1
                logger.error(error_msg)
        
        # Update task with final results
        update_zip_task(
            task_id,
            status=TaskStatus.COMPLETED,
            completed_folders=successful_zips,
            failed_folders=failed_zips,
            results=results
        )
        
        logger.info(f"Zip task {task_id} completed: {successful_zips} successful, {failed_zips} failed")
        
        # Execute callback if provided
        callback = task_data.get("callback")
        if callback:
            execute_callback(task_id, callback, task_data, results)
        
    except Exception as e:
        error_msg = f"Zip task failed: {str(e)}"
        update_zip_task(
            task_id,
            status=TaskStatus.FAILED,
            error_message=error_msg
        )
        logger.error(f"Zip task {task_id} failed: {error_msg}")
        
        # Execute callback even on failure if provided
        callback = task_data.get("callback")
        if callback:
            execute_callback(task_id, callback, task_data)


def execute_callback(task_id: str, callback, task_data: dict, results: list[dict] | None = None) -> None:
    """Execute the callback HTTP request exactly as configured."""
    try:
        # Prepare headers
        headers = callback.headers or {}
        if "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"
        
        # Get method and body from the CallbackRequest object
        method = callback.method.upper()
        body_data = callback.body or {}
        body_data['results'] = results
        
        # Make the callback request with only the configured data
        if method == "GET":
            response = requests.get(
                callback.url,
                headers=headers,
                params=body_data,
                timeout=30
            )
        else:  # POST, PUT, PATCH, etc.
            response = requests.request(
                method,
                callback.url,
                headers=headers,
                json=body_data,
                timeout=30
            )
        
        if response.status_code < 400:
            logger.info(f"Callback executed successfully for task {task_id}: {response.status_code}")
        else:
            logger.warning(f"Callback returned error status for task {task_id}: {response.status_code} - {response.text}")
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to execute callback for task {task_id}: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error executing callback for task {task_id}: {str(e)}")


def zip_and_upload_folders_async(request: ZipAndUploadRequest) -> ZipTaskResponse:
    """Start background zipping and uploading of multiple folders."""
    if not request.folders:
        raise ValueError("No folders specified for zipping")
    
    # Create zip task with callback
    task_id = create_zip_task(request.folders, request.callback)
    
    return ZipTaskResponse(
        task_id=task_id,
        status=TaskStatus.PENDING,
        total_folders=len(request.folders),
        successful_zips=0,
        failed_zips=0,
        results=[]
    )


def zip_and_upload_folders(request: ZipAndUploadRequest) -> ZipAndUploadResponse:
    """Zip and upload multiple folders synchronously."""
    if not request.folders:
        raise ValueError("No folders specified for zipping")
    
    results: List[FolderZipResult] = []
    successful_zips = 0
    failed_zips = 0
    
    for folder_item in request.folders:
        try:
            result = process_single_folder_zip(folder_item)
            results.append(result)
            
            if result.status == "success":
                successful_zips += 1
                logger.info(f"Successfully zipped folder {folder_item.s3_path}")
            else:
                failed_zips += 1
                logger.error(f"Failed to zip folder {folder_item.s3_path}: {result.error_message}")
                
        except Exception as e:
            error_msg = f"Unexpected error processing {folder_item.s3_path}: {str(e)}"
            results.append(FolderZipResult(
                s3_path=folder_item.s3_path,
                s3_zip_path=folder_item.s3_zip_path,
                status="failed",
                error_message=error_msg,
                files_count=0,
                excluded_subfolders=[]
            ))
            failed_zips += 1
            logger.error(error_msg)
    
    return ZipAndUploadResponse(
        total_folders=len(request.folders),
        successful_zips=successful_zips,
        failed_zips=failed_zips,
        results=results
    )