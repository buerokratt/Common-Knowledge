from fastapi import FastAPI, BackgroundTasks

from api.models import EntityToClean
from worker.tasks import clean_file_task

app = FastAPI()


@app.post("/clean_file", status_code=202)
def clean_file(entity: EntityToClean, background_tasks: BackgroundTasks):
    """
    Accept a cleaning job and run it in the background.
    Returns 202 Accepted immediately; the task runs asynchronously.
    """
    background_tasks.add_task(clean_file_task, entity)
    return {"status": "accepted"}