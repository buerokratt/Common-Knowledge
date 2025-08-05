from fastapi import APIRouter, BackgroundTasks, HTTPException
from app.schemas import (
    DeleteFilesRequest,
    DeleteFilesResponse,
    DeleteTaskResponse,
    DeleteTaskStatusResponse
)
from app.services import delete_service

router = APIRouter()


@router.post("/delete-files", response_model=DeleteFilesResponse)
def delete_files(request: DeleteFilesRequest) -> DeleteFilesResponse:
    """Delete multiple files/folders from blob storage (synchronous)."""
    try:
        return delete_service.delete_files(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete files: {str(e)}")


@router.post("/delete-files-async", response_model=DeleteTaskResponse)
def delete_files_async(request: DeleteFilesRequest, background_tasks: BackgroundTasks) -> DeleteTaskResponse:
    """Start background deletion of multiple files/folders from blob storage."""
    try:
        task_response = delete_service.delete_files_async(request)
        background_tasks.add_task(delete_service.process_delete_task, task_response.task_id)
        return task_response
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start delete task: {str(e)}")


@router.get("/delete-task/{task_id}", response_model=DeleteTaskStatusResponse)
def get_delete_task_status(task_id: str) -> DeleteTaskStatusResponse:
    """Get the status of a delete task."""
    task_data = delete_service.get_delete_task(task_id)
    if not task_data:
        raise HTTPException(status_code=404, detail="Delete task not found")
    
    return DeleteTaskStatusResponse(**task_data)