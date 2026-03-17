import multiprocessing

from fastapi import FastAPI

from api.models import EntityToClean, SourceCleaningTask
from worker.tasks import clean_file_task, clean_source_task



app = FastAPI()


@app.post('/clean_file')
def clean_file(entity: EntityToClean):
    process = multiprocessing.Process(target=clean_file_task, args=(entity,))
    process.start()
    process.join()

@app.post('/clean_source_async')
def clean_source_async(task: SourceCleaningTask):
    process = multiprocessing.Process(target=clean_source_task, args=(task,))
    process.start()
    return {"status": "started", "source_run_report_base_id": task.source_run_report_base_id}
