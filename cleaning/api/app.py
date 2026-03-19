from fastapi import FastAPI
 
from api.models import EntityToClean
from worker.tasks import clean_file_task
 
app = FastAPI()
 
 
@app.post("/clean_file")
def clean_file(entity: EntityToClean):
    """
    Accept a cleaning job and run it synchronously.
    Returns the result of the cleaning operation once it has completed.
    """
    return clean_file_task(entity)
 