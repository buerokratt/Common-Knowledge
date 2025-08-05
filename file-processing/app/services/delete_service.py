import os
import logging
import uuid
import requests
from typing import List, Dict, Optional
from datetime import datetime
from app.schemas import (
    DeleteFilesRequest,
    DeleteFilesResponse,
    BlobFileDeleteItem,
    FileDeleteItem,
    FileDeleteResult,
    BlobFileDeleteResult,
    DeleteTaskResponse,
    DeleteTaskStatusResponse,
    TaskStatus
)
from app.services.blob_storage import storage_provider, BlobStorageException

logger = logging.getLogger(__name__)

# In-memory task store for delete tasks
_delete_tasks: Dict[str, dict] = {}


def create_delete_task(files: List[BlobFileDeleteItem], callback: Optional = None) -> str:
    """Create a new delete task in memory."""
    task_id = str(uuid.uuid4())
    
    _delete_tasks[task_id] = {
        "task_id": task_id,
        "status": TaskStatus.PENDING,
        "files": files,
        "callback": callback,
        "total_files": len(files),
        "completed_files": 0,
        "failed_files": 0,
        "results": [],
        "error_message": None,
        "created_at": datetime.now(),
        "updated_at": datetime.now()
    }
    
    return task_id


def get_delete_task(task_id: str) -> Optional[dict]:
    """Get delete task status by task ID."""
    return _delete_tasks.get(task_id)


def update_delete_task(task_id: str, **updates) -> None:
    """Update delete task status and related fields."""
    if task_id in _delete_tasks:
        for key, value in updates.items():
            _delete_tasks[task_id][key] = value
        _delete_tasks[task_id]["updated_at"] = datetime.now()


def process_single_file_delete(file_item: BlobFileDeleteItem) -> BlobFileDeleteResult:
    """Process deletion of a single file."""
    try:
        # Clean the s3_path - remove s3:// prefix if present
        clean_s3_path = file_item.s3_path
        if clean_s3_path.startswith('s3://'):
            parts = clean_s3_path.replace('s3://', '').split('/', 1)
            if len(parts) > 1:
                clean_s3_path = parts[1]
            else:
                clean_s3_path = parts[0]
        
        # Check if source file exists
        if not storage_provider.file_exists(clean_s3_path):
            return BlobFileDeleteResult(
                s3_path=file_item.s3_path,
                status="failed",
                error_message="File does not exist"
            )
        
        # Delete the file
        success = storage_provider.delete_file(clean_s3_path)
        
        if success:
            return BlobFileDeleteResult(
                s3_path=file_item.s3_path,
                status="success"
            )
        else:
            return BlobFileDeleteResult(
                s3_path=file_item.s3_path,
                status="failed",
                error_message="Delete operation failed"
            )
            
    except BlobStorageException as e:
        return BlobFileDeleteResult(
            s3_path=file_item.s3_path,
            status="failed",
            error_message=f"Blob storage error: {str(e)}"
        )
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        return BlobFileDeleteResult(
            s3_path=file_item.s3_path,
            status="failed",
            error_message=error_msg
        )


def process_folder_delete(file_item: FileDeleteItem) -> FileDeleteResult:
    """Process deletion of a folder (all files within it)."""
    try:
        # Clean the s3_path - remove s3:// prefix if present
        clean_s3_path = file_item.s3_path
        if clean_s3_path.startswith('s3://'):
            parts = clean_s3_path.replace('s3://', '').split('/', 1)
            if len(parts) > 1:
                clean_s3_path = parts[1]
            else:
                clean_s3_path = parts[0]
        
        # Ensure path ends with / for proper folder handling
        if not clean_s3_path.endswith('/'):
            clean_s3_path += '/'
        
        # Delete the entire folder
        success = storage_provider.delete_folder(clean_s3_path)
        
        if success:
            return BlobFileDeleteResult(
                s3_path=file_item.s3_path,
                status="success"
            )
        else:
            return BlobFileDeleteResult(
                s3_path=file_item.s3_path,
                status="failed",
                error_message="Folder delete operation failed"
            )
            
    except BlobStorageException as e:
        return BlobFileDeleteResult(
            s3_path=file_item.s3_path,
            status="failed",
            error_message=f"Blob storage error: {str(e)}"
        )
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        return BlobFileDeleteResult(
            s3_path=file_item.s3_path,
            status="failed",
            error_message=error_msg
        )


def process_delete_task(task_id: str) -> None:
    """Process a delete task by deleting all files."""
    task_data = _delete_tasks.get(task_id)
    if not task_data:
        logger.error(f"Delete task {task_id} not found")
        return

    try:
        update_delete_task(task_id, status=TaskStatus.PROCESSING)
        
        results: List[BlobFileDeleteResult] = []
        successful_deletions = 0
        failed_deletions = 0
        
        for file_item in task_data["files"]:
            try:
                if file_item.is_folder:
                    result = process_folder_delete(file_item)
                    logger.info(f"Processed folder delete {file_item.s3_path}: {result.status}")
                else:
                    result = process_single_file_delete(file_item)
                    logger.info(f"Processed file delete {file_item.s3_path}: {result.status}")
                
                results.append(result)
                
                if result.status == "success":
                    successful_deletions += 1
                else:
                    failed_deletions += 1
                    
            except Exception as e:
                error_msg = f"Unexpected error processing {file_item.s3_path}: {str(e)}"
                results.append(BlobFileDeleteResult(
                    s3_path=file_item.s3_path,
                    status="failed",
                    error_message=error_msg
                ))
                failed_deletions += 1
                logger.error(error_msg)
        
        # Update task with final results
        update_delete_task(
            task_id,
            status=TaskStatus.COMPLETED,
            completed_files=successful_deletions,
            failed_files=failed_deletions,
            results=results
        )
        
        logger.info(f"Delete task {task_id} completed: {successful_deletions} successful, {failed_deletions} failed")
        
        # Execute callback if provided
        callback = task_data.get("callback")
        if callback:
            execute_callback(task_id, callback, task_data)
        
    except Exception as e:
        error_msg = f"Delete task failed: {str(e)}"
        update_delete_task(
            task_id,
            status=TaskStatus.FAILED,
            error_message=error_msg
        )
        logger.error(f"Delete task {task_id} failed: {error_msg}")
        
        # Execute callback even on failure if provided
        callback = task_data.get("callback")
        if callback:
            execute_callback(task_id, callback, task_data)


def execute_callback(task_id: str, callback, task_data: dict) -> None:
    """Execute the callback HTTP request exactly as configured."""
    try:
        # Prepare headers
        headers = callback.headers or {}
        if "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"
        
        # Get method and body from the CallbackRequest object
        method = callback.method.upper()
        body_data = callback.body or {}
        
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


def delete_files_async(request: DeleteFilesRequest) -> DeleteTaskResponse:
    """Start background deletion of multiple files."""
    if not request.files:
        raise ValueError("No files specified for deletion")
    
    # Create delete task with callback
    task_id = create_delete_task(request.files, request.callback)
    
    return DeleteTaskResponse(
        task_id=task_id,
        status=TaskStatus.PENDING,
        total_files=len(request.files),
        successful_deletions=0,
        failed_deletions=0,
        results=[]
    )


def delete_files(request: DeleteFilesRequest) -> DeleteFilesResponse:
    """Delete multiple files synchronously."""
    if not request.files:
        raise ValueError("No files specified for deletion")
    
    results: List[BlobFileDeleteResult] = []
    successful_deletions = 0
    failed_deletions = 0
    
    for file_item in request.files:
        try:
            if file_item.is_folder:
                result = process_folder_delete(file_item)
                logger.info(f"Processed folder delete {file_item.s3_path}: {result.status}")
            else:
                result = process_single_file_delete(file_item)
                logger.info(f"Processed file delete {file_item.s3_path}: {result.status}")
            
            results.append(result)
            
            if result.status == "success":
                successful_deletions += 1
            else:
                failed_deletions += 1
                
        except Exception as e:
            error_msg = f"Unexpected error processing {file_item.s3_path}: {str(e)}"
            results.append(BlobFileDeleteResult(
                s3_path=file_item.s3_path,
                status="failed",
                error_message=error_msg
            ))
            failed_deletions += 1
            logger.error(error_msg)
    
    return DeleteFilesResponse(
        total_files=len(request.files),
        successful_deletions=successful_deletions,
        failed_deletions=failed_deletions,
        results=results
    )