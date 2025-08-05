import os
import logging
import uuid
import requests
from typing import List, Dict, Optional
from datetime import datetime
from app.schemas import (
    MoveFilesRequest,
    MoveFilesResponse,
    FileMoveItem,
    FileMoveResult,
    MoveTaskResponse,
    MoveTaskStatusResponse,
    TaskStatus
)
from app.services.blob_storage import storage_provider, BlobStorageException

logger = logging.getLogger(__name__)

# In-memory task store for move tasks
_move_tasks: Dict[str, dict] = {}


def create_move_task(files: List[FileMoveItem], callback: Optional = None) -> str:
    """Create a new move task in memory."""
    task_id = str(uuid.uuid4())
    
    _move_tasks[task_id] = {
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


def get_move_task(task_id: str) -> Optional[dict]:
    """Get move task status by task ID."""
    return _move_tasks.get(task_id)


def update_move_task(task_id: str, **updates) -> None:
    """Update move task status and related fields."""
    if task_id in _move_tasks:
        for key, value in updates.items():
            _move_tasks[task_id][key] = value
        _move_tasks[task_id]["updated_at"] = datetime.now()


def process_single_file_move(file_item: FileMoveItem) -> FileMoveResult:
    """Process move of a single file."""
    try:
        # Clean the s3_from_path - remove s3:// prefix if present
        clean_from_path = file_item.s3_from_path
        if clean_from_path.startswith('s3://'):
            parts = clean_from_path.replace('s3://', '').split('/', 1)
            if len(parts) > 1:
                clean_from_path = parts[1]
            else:
                clean_from_path = parts[0]
        
        # Clean the s3_to_path - remove s3:// prefix if present
        clean_to_path = file_item.s3_to_path
        if clean_to_path.startswith('s3://'):
            parts = clean_to_path.replace('s3://', '').split('/', 1)
            if len(parts) > 1:
                clean_to_path = parts[1]
            else:
                clean_to_path = parts[0]
        
        # Check if source file exists
        if not storage_provider.file_exists(clean_from_path):
            return FileMoveResult(
                s3_from_path=file_item.s3_from_path,
                s3_to_path=file_item.s3_to_path,
                status="failed",
                error_message="Source file does not exist"
            )
        
        # Check if destination already exists (optional - you might want to overwrite)
        if storage_provider.file_exists(clean_to_path):
            logger.warning(f"Destination file {clean_to_path} already exists, will be overwritten")
        
        # Move the file (copy then delete)
        success = storage_provider.move_file(clean_from_path, clean_to_path)
        
        if success:
            return FileMoveResult(
                s3_from_path=file_item.s3_from_path,
                s3_to_path=file_item.s3_to_path,
                status="success"
            )
        else:
            return FileMoveResult(
                s3_from_path=file_item.s3_from_path,
                s3_to_path=file_item.s3_to_path,
                status="failed",
                error_message="Move operation failed"
            )
            
    except BlobStorageException as e:
        return FileMoveResult(
            s3_from_path=file_item.s3_from_path,
            s3_to_path=file_item.s3_to_path,
            status="failed",
            error_message=f"Blob storage error: {str(e)}"
        )
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        return FileMoveResult(
            s3_from_path=file_item.s3_from_path,
            s3_to_path=file_item.s3_to_path,
            status="failed",
            error_message=error_msg
        )


def process_folder_move(file_item: FileMoveItem) -> FileMoveResult:
    """Process move of a folder (all files within it)."""
    try:
        # Clean the s3_from_path - remove s3:// prefix if present
        clean_from_path = file_item.s3_from_path
        if clean_from_path.startswith('s3://'):
            parts = clean_from_path.replace('s3://', '').split('/', 1)
            if len(parts) > 1:
                clean_from_path = parts[1]
            else:
                clean_from_path = parts[0]
        
        # Clean the s3_to_path - remove s3:// prefix if present
        clean_to_path = file_item.s3_to_path
        if clean_to_path.startswith('s3://'):
            parts = clean_to_path.replace('s3://', '').split('/', 1)
            if len(parts) > 1:
                clean_to_path = parts[1]
            else:
                clean_to_path = parts[0]
        
        # Ensure paths end with / for proper folder handling
        if not clean_from_path.endswith('/'):
            clean_from_path += '/'
        if not clean_to_path.endswith('/'):
            clean_to_path += '/'
        
        # Move the entire folder
        success = storage_provider.move_folder(clean_from_path, clean_to_path)
        
        if success:
            return FileMoveResult(
                s3_from_path=file_item.s3_from_path,
                s3_to_path=file_item.s3_to_path,
                status="success"
            )
        else:
            return FileMoveResult(
                s3_from_path=file_item.s3_from_path,
                s3_to_path=file_item.s3_to_path,
                status="failed",
                error_message="Folder move operation failed"
            )
            
    except BlobStorageException as e:
        return FileMoveResult(
            s3_from_path=file_item.s3_from_path,
            s3_to_path=file_item.s3_to_path,
            status="failed",
            error_message=f"Blob storage error: {str(e)}"
        )
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        return FileMoveResult(
            s3_from_path=file_item.s3_from_path,
            s3_to_path=file_item.s3_to_path,
            status="failed",
            error_message=error_msg
        )


def process_move_task(task_id: str) -> None:
    """Process a move task by moving all files."""
    task_data = _move_tasks.get(task_id)
    if not task_data:
        logger.error(f"Move task {task_id} not found")
        return

    try:
        update_move_task(task_id, status=TaskStatus.PROCESSING)
        
        results: List[FileMoveResult] = []
        successful_moves = 0
        failed_moves = 0
        
        for file_item in task_data["files"]:
            try:
                if file_item.is_folder:
                    result = process_folder_move(file_item)
                    logger.info(f"Processed folder move {file_item.s3_from_path}: {result.status}")
                else:
                    result = process_single_file_move(file_item)
                    logger.info(f"Processed file move {file_item.s3_from_path}: {result.status}")
                
                results.append(result)
                
                if result.status == "success":
                    successful_moves += 1
                else:
                    failed_moves += 1
                    
            except Exception as e:
                error_msg = f"Unexpected error processing {file_item.s3_from_path}: {str(e)}"
                results.append(FileMoveResult(
                    s3_from_path=file_item.s3_from_path,
                    s3_to_path=file_item.s3_to_path,
                    status="failed",
                    error_message=error_msg
                ))
                failed_moves += 1
                logger.error(error_msg)
        
        # Update task with final results
        update_move_task(
            task_id,
            status=TaskStatus.COMPLETED,
            completed_files=successful_moves,
            failed_files=failed_moves,
            results=results
        )
        
        logger.info(f"Move task {task_id} completed: {successful_moves} successful, {failed_moves} failed")
        
        # Execute callback if provided
        callback = task_data.get("callback")
        if callback:
            execute_callback(task_id, callback, task_data)
        
    except Exception as e:
        error_msg = f"Move task failed: {str(e)}"
        update_move_task(
            task_id,
            status=TaskStatus.FAILED,
            error_message=error_msg
        )
        logger.error(f"Move task {task_id} failed: {error_msg}")
        
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


def move_files_async(request: MoveFilesRequest) -> MoveTaskResponse:
    """Start background move of multiple files."""
    if not request.files:
        raise ValueError("No files specified for move")
    
    # Create move task with callback
    task_id = create_move_task(request.files, request.callback)
    
    return MoveTaskResponse(
        task_id=task_id,
        status=TaskStatus.PENDING,
        total_files=len(request.files),
        successful_moves=0,
        failed_moves=0,
        results=[]
    )


def move_files(request: MoveFilesRequest) -> MoveFilesResponse:
    """Move multiple files synchronously."""
    if not request.files:
        raise ValueError("No files specified for move")
    
    results: List[FileMoveResult] = []
    successful_moves = 0
    failed_moves = 0
    
    for file_item in request.files:
        try:
            if file_item.is_folder:
                result = process_folder_move(file_item)
                logger.info(f"Processed folder move {file_item.s3_from_path}: {result.status}")
            else:
                result = process_single_file_move(file_item)
                logger.info(f"Processed file move {file_item.s3_from_path}: {result.status}")
            
            results.append(result)
            
            if result.status == "success":
                successful_moves += 1
            else:
                failed_moves += 1
                
        except Exception as e:
            error_msg = f"Unexpected error processing {file_item.s3_from_path}: {str(e)}"
            results.append(FileMoveResult(
                s3_from_path=file_item.s3_from_path,
                s3_to_path=file_item.s3_to_path,
                status="failed",
                error_message=error_msg
            ))
            failed_moves += 1
            logger.error(error_msg)
    
    return MoveFilesResponse(
        total_files=len(request.files),
        successful_moves=successful_moves,
        failed_moves=failed_moves,
        results=results
    )