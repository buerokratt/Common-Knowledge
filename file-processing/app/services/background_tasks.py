from app.services import upload_service


def upload_task(task_id: str) -> None:
    """Background task for processing file uploads."""
    upload_service.process_task(task_id)