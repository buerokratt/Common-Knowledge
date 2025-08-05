import uuid
import os
import logging
from typing import Optional, Dict
from datetime import datetime, timedelta
from app.schemas import (
    TaskStatus, 
    UploadUrlRequest, 
    UploadUrlResponse, 
    UploadUrlItem, 
    UploadTaskStatusResponse,
    FileContentUploadRequest,
    FileContentUploadResponse
)
from app.services.blob_storage import storage_provider, BlobStorageException
from app.core.config import settings

logger = logging.getLogger(__name__)

# In-memory task store
_tasks: Dict[str, dict] = {}


def create_task(source_file_path: str) -> str:
    """Create a new upload task in memory."""
    task_id = str(uuid.uuid4())
    
    _tasks[task_id] = {
        "task_id": task_id,
        "status": TaskStatus.PENDING,
        "source_file_path": source_file_path,
        "blob_storage_path": None,
        "error_message": None,
        "created_at": datetime.now(),
        "updated_at": datetime.now()
    }
    
    return task_id


def get_task(task_id: str) -> Optional[UploadTaskStatusResponse]:
    """Get task status by task ID."""
    task_data = _tasks.get(task_id)
    if not task_data:
        return None
    
    return UploadTaskStatusResponse(**task_data)


def update_task(task_id: str, status: TaskStatus, blob_storage_path: Optional[str] = None, error_message: Optional[str] = None) -> None:
    """Update task status and related fields."""
    if task_id in _tasks:
        _tasks[task_id]["status"] = status
        _tasks[task_id]["updated_at"] = datetime.now()
        
        if blob_storage_path:
            _tasks[task_id]["blob_storage_path"] = blob_storage_path
        if error_message:
            _tasks[task_id]["error_message"] = error_message


def process_task(task_id: str) -> None:
    """Process an upload task by uploading the file to blob storage."""
    task_data = _tasks.get(task_id)
    if not task_data:
        logger.error(f"Task {task_id} not found")
        return

    try:
        update_task(task_id, TaskStatus.PROCESSING)
        
        full_source_path = os.path.join(settings.source_path, task_data["source_file_path"])
        
        # Check if source file exists
        if not os.path.exists(full_source_path):
            update_task(task_id, TaskStatus.FAILED, error_message=f"Source file not found: {full_source_path}")
            return
        
        destination_path = f"uploads/{task_id}/{os.path.basename(task_data['source_file_path'])}"
        
        blob_storage_path = storage_provider.upload_file(full_source_path, destination_path)
        update_task(task_id, TaskStatus.COMPLETED, blob_storage_path=blob_storage_path)
        
        logger.info(f"Successfully uploaded task {task_id} to {blob_storage_path}")
        
    except BlobStorageException as e:
        error_msg = f"Blob storage error: {str(e)}"
        update_task(task_id, TaskStatus.FAILED, error_message=error_msg)
        logger.error(f"Task {task_id} failed: {error_msg}")
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        update_task(task_id, TaskStatus.FAILED, error_message=error_msg)
        logger.error(f"Task {task_id} failed: {error_msg}")


def generate_upload_urls(request: UploadUrlRequest) -> UploadUrlResponse:
    """Generate presigned upload URLs for multiple blob paths with individual content types."""
    try:
        if not request.files:
            raise ValueError("No files provided")
        
        upload_url_items = []
        
        for file_info in request.files:
            path = file_info.path
            content_type = file_info.content_type
            
            # Generate single upload URL with specific content type
            upload_url_tuples = storage_provider.generate_upload_urls(
                paths=[path],
                content_type=content_type,
                expires_in=request.expires_in
            )
            
            if upload_url_tuples:
                file_path, upload_url, expires_at = upload_url_tuples[0]
                upload_url_items.append(
                    UploadUrlItem(
                        path=file_path,
                        upload_url=upload_url,
                        expires_at=expires_at
                    )
                )
        
        return UploadUrlResponse(upload_urls=upload_url_items)
        
    except BlobStorageException as e:
        raise ValueError(f"Blob storage error: {str(e)}")
    except Exception as e:
        raise ValueError(f"Failed to generate upload URLs: {str(e)}")


def upload_file_sync(source_file_path: str) -> str:
    """Upload a file to blob storage synchronously."""
    try:
        full_source_path = os.path.join(settings.source_path, source_file_path)
        
        if not os.path.exists(full_source_path):
            raise ValueError(f"Source file not found: {full_source_path}")
        
        destination_path = f"uploads{source_file_path}"
        
        return storage_provider.upload_file(full_source_path, destination_path)
        
    except BlobStorageException as e:
        raise ValueError(f"Blob storage error: {str(e)}")
    except Exception as e:
        raise ValueError(f"Failed to upload file: {str(e)}")


def clean_path(path: str) -> str:
    return storage_provider.clean_path(path)


def upload_file_content(request: FileContentUploadRequest) -> FileContentUploadResponse:
    """Upload file content directly to blob storage."""
    try:
        import base64
        
        # Decode base64 content
        try:
            file_content = base64.b64decode(request.file_content)
        except Exception as e:
            raise ValueError(f"Invalid base64 content: {str(e)}")
        
        # Clean the file_path - remove any s3:// prefix if present
        clean_file_path = request.file_path
        if clean_file_path.startswith('s3://'):
            parts = clean_file_path.replace('s3://', '').split('/', 1)
            if len(parts) > 1:
                clean_file_path = parts[1]
            else:
                clean_file_path = parts[0]
        
        # Upload content to blob storage
        blob_storage_path = storage_provider.upload_file_content(
            file_content, 
            clean_file_path, 
            request.content_type
        )
        
        return FileContentUploadResponse(
            blob_storage_path=blob_storage_path,
            status="completed",
            file_size=len(file_content)
        )
        
    except BlobStorageException as e:
        raise ValueError(f"Blob storage error: {str(e)}")
    except Exception as e:
        raise ValueError(f"Failed to upload file content: {str(e)}")


def cleanup_old_tasks(max_age_hours: int = 24) -> int:
    """Clean up old tasks from memory."""
    cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
    
    # Find tasks older than cutoff_time
    tasks_to_remove = [
        task_id for task_id, task in _tasks.items()
        if task["updated_at"] < cutoff_time
    ]
    
    # Remove old tasks
    for task_id in tasks_to_remove:
        del _tasks[task_id]
    
    logger.info(f"Cleaned up {len(tasks_to_remove)} old tasks")
    return len(tasks_to_remove)


def get_task_stats() -> dict:
    """Get statistics about tasks in memory."""
    if not _tasks:
        return {"total": 0, "by_status": {}}
    
    stats = {"total": len(_tasks), "by_status": {}}
    
    for task in _tasks.values():
        status = task["status"]
        stats["by_status"][status] = stats["by_status"].get(status, 0) + 1
    
    return stats