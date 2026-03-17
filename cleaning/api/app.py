import multiprocessing

from fastapi import FastAPI

from api.models import EntityToClean
from worker.tasks import clean_file_task


app = FastAPI()


@app.post('/clean_file')
def clean_file(entity: EntityToClean):
    process = multiprocessing.Process(target=clean_file_task, args=(entity,))
    process.start()
    process.join()
