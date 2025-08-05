from fastapi import APIRouter, BackgroundTasks, HTTPException
from app.schemas import (
    DownloadFileRequest, 
    DownloadFileResponse,
    DownloadToVolumeRequest,
    DownloadToVolumeResponse,
    DownloadTaskResponse,
    DownloadTaskStatusResponse,
    DeleteFromVolumeRequest,
    DeleteFromVolumeResponse
)
from app.services import download_service

router = APIRouter()


@router.post("/download-urls", response_model=DownloadFileResponse)
def generate_download_urls(request: DownloadFileRequest) -> DownloadFileResponse:
    print(request)
    """Generate presigned download URLs for multiple files in blob storage."""
    try:
        return download_service.generate_download_urls(request.paths)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate download URLs: {str(e)}")
    
@router.post("/download-files-to-volume", response_model=DownloadToVolumeResponse)
def download_files_to_volume(request: DownloadToVolumeRequest) -> DownloadToVolumeResponse:
    """Download multiple files from blob storage to local volume (synchronous)."""
    try:
        return download_service.download_files_to_volume(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to download files: {str(e)}")


@router.post("/download-files-to-volume-async", response_model=DownloadTaskResponse)
def download_files_to_volume_async(request: DownloadToVolumeRequest, background_tasks: BackgroundTasks) -> DownloadTaskResponse:
    """Start background download of multiple files from blob storage to local volume."""
    try:
        task_response = download_service.download_files_to_volume_async(request)
        background_tasks.add_task(download_service.process_download_task, task_response.task_id)
        return task_response
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start download task: {str(e)}")


@router.get("/download-task/{task_id}", response_model=DownloadTaskStatusResponse)
def get_download_task_status(task_id: str) -> DownloadTaskStatusResponse:
    """Get the status of a download task."""
    task_data = download_service.get_download_task(task_id)
    if not task_data:
        raise HTTPException(status_code=404, detail="Download task not found")
    
    return DownloadTaskStatusResponse(**task_data)


@router.post("/delete-files-from-volume", response_model=DeleteFromVolumeResponse)
def delete_files_from_volume(request: DeleteFromVolumeRequest) -> DeleteFromVolumeResponse:
    """Delete multiple files from local volume."""
    try:
        return download_service.delete_files_from_volume(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete files: {str(e)}")