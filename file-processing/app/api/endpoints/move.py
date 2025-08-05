from fastapi import APIRouter, BackgroundTasks, HTTPException
from app.schemas import (
    MoveFilesRequest,
    MoveFilesResponse,
    MoveTaskResponse,
    MoveTaskStatusResponse
)
from app.services import move_service

router = APIRouter()


@router.post("/move-files", response_model=MoveFilesResponse)
def move_files(request: MoveFilesRequest) -> MoveFilesResponse:
    """Move multiple files in blob storage (synchronous)."""
    try:
        return move_service.move_files(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to move files: {str(e)}")


@router.post("/move-files-async", response_model=MoveTaskResponse)
def move_files_async(request: MoveFilesRequest, background_tasks: BackgroundTasks) -> MoveTaskResponse:
    """Start background move of multiple files in blob storage."""
    try:
        task_response = move_service.move_files_async(request)
        background_tasks.add_task(move_service.process_move_task, task_response.task_id)
        return task_response
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start move task: {str(e)}")


@router.get("/move-task/{task_id}", response_model=MoveTaskStatusResponse)
def get_move_task_status(task_id: str) -> MoveTaskStatusResponse:
    """Get the status of a move task."""
    task_data = move_service.get_move_task(task_id)
    if not task_data:
        raise HTTPException(status_code=404, detail="Move task not found")
    
    return MoveTaskStatusResponse(**task_data)