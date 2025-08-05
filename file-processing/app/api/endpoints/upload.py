from fastapi import APIRouter, BackgroundTasks, HTTPException
from app.schemas import (
    FileUploadRequest, 
    FileUploadResponse, 
    UploadTaskStatusResponse,
    UploadUrlRequest, 
    UploadUrlResponse,
    FileContentUploadRequest,
    FileContentUploadResponse
)
from app.services import upload_service

router = APIRouter()


@router.post("/upload-urls", response_model=UploadUrlResponse)
def generate_upload_urls(request: UploadUrlRequest) -> UploadUrlResponse:
    """Generate presigned upload URLs for multiple blob paths."""
    try:
        return upload_service.generate_upload_urls(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate upload URLs: {str(e)}")


@router.post("/upload", response_model=FileUploadResponse)
def upload_file(request: FileUploadRequest, background_tasks: BackgroundTasks) -> FileUploadResponse:
    """Upload a file with task tracking (in-memory)."""
    task_id = upload_service.create_task(request.source_file_path)
    background_tasks.add_task(upload_service.process_task, task_id)
    return FileUploadResponse(task_id=task_id, status="pending")


@router.get("/upload/{task_id}", response_model=UploadTaskStatusResponse)
def get_task_status(task_id: str) -> UploadTaskStatusResponse:
    """Get the status of an upload task."""
    task = upload_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/upload-sync")
def upload_file_sync(request: FileUploadRequest) -> dict:
    """Upload a file synchronously."""
    try:
        blob_storage_path = upload_service.upload_file_sync(request.source_file_path)
        return {
            "blob_storage_path": blob_storage_path,
            "source_file_path": upload_service.clean_path(blob_storage_path),
            "status": "completed",
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload file: {str(e)}")


@router.post("/upload-file-content", response_model=FileContentUploadResponse)
def upload_file_content(request: FileContentUploadRequest) -> FileContentUploadResponse:
    """Upload file content directly to blob storage."""
    try:
        return upload_service.upload_file_content(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload file content: {str(e)}")


@router.get("/tasks/stats")
def get_task_statistics() -> dict:
    """Get statistics about tasks in memory."""
    return upload_service.get_task_stats()


@router.delete("/tasks/cleanup")
def cleanup_old_tasks(max_age_hours: int = 24) -> dict:
    """Clean up old tasks from memory."""
    removed_count = upload_service.cleanup_old_tasks(max_age_hours)
    return {"message": f"Cleaned up {removed_count} old tasks"}