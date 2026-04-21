import multiprocessing

from fastapi import FastAPI, HTTPException

from api.models import EntityToClean, SourceCleaningTask
from worker.tasks import clean_file_task, clean_source_task

app = FastAPI()

# Maximum time to wait for a synchronous cleaning job before giving up
_CLEAN_FILE_TIMEOUT_SECONDS = 600


@app.post("/clean_file")
def clean_file(entity: EntityToClean) -> dict[str, str]:
    """
    Accept a single-file cleaning job and run it synchronously.
    Blocks until the file has been cleaned and uploaded, or until the
    timeout is reached.
    """
    process = multiprocessing.Process(target=clean_file_task, args=(entity,))
    process.start()
    process.join(timeout=_CLEAN_FILE_TIMEOUT_SECONDS)

    if process.is_alive():
        process.terminate()
        process.join()
        raise HTTPException(status_code=504, detail="Cleaning task timed out")

    if process.exitcode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"Cleaning task failed with exit code {process.exitcode}",
        )

    return {"status": "ok"}


@app.post("/clean_source_async")
def clean_source_async(task: SourceCleaningTask) -> dict[str, str]:
    """
    Accept a batch cleaning job and return immediately.
    Processing continues in the background.
    """
    process = multiprocessing.Process(target=clean_source_task, args=(task,))
    process.start()
    return {
        "status": "started",
        "source_run_report_base_id": task.source_run_report_base_id,
    }
