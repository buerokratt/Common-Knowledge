from fastapi import APIRouter, BackgroundTasks, HTTPException
from app.schemas import (
    ZipAndUploadRequest,
    ZipAndUploadResponse,
    ZipTaskResponse,
    ZipTaskStatusResponse
)
from app.services import zip_service

router = APIRouter()


@router.post("/zip-and-upload-folders", response_model=ZipAndUploadResponse)
def zip_and_upload_folders(request: ZipAndUploadRequest) -> ZipAndUploadResponse:
    """Zip folders from S3 and upload as zip files (synchronous)."""
    try:
        return zip_service.zip_and_upload_folders(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to zip and upload folders: {str(e)}")


@router.post("/zip-and-upload-folders-async", response_model=ZipTaskResponse)
def zip_and_upload_folders_async(request: ZipAndUploadRequest, background_tasks: BackgroundTasks) -> ZipTaskResponse:
    """Start background zipping and uploading of folders from S3."""
    try:
        task_response = zip_service.zip_and_upload_folders_async(request)
        background_tasks.add_task(zip_service.process_zip_task, task_response.task_id)
        return task_response
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start zip task: {str(e)}")


@router.get("/zip-task/{task_id}", response_model=ZipTaskStatusResponse)
def get_zip_task_status(task_id: str) -> ZipTaskStatusResponse:
    """Get the status of a zip task."""
    task_data = zip_service.get_zip_task(task_id)
    if not task_data:
        raise HTTPException(status_code=404, detail="Zip task not found")
    
    return ZipTaskStatusResponse(**task_data)