import json
import os
from datetime import datetime
from pathlib import Path
from scrapper import settings

import requests
from fastapi import FastAPI

from api.models import (
    SpecifiedLinksScrapeTask, SitemapCollectScrapperTask, EntireSourceScrapperTask, UploadedFileTask,
    LinkToScrape, EditedMetadataTask, SpecifiedApiFilesScrapeTask
)
from worker.tasks import (
    specified_links_scrapper_task, sitemap_collect_scrapper_task,
    entire_source_scrapped_task, uploaded_file_task, specified_api_files_scrapper_task
)
from api.models import EestiScrapperTask
from worker.tasks import eesti_scrapper_task

app = FastAPI()


@app.post('/specified-pages-scrapper-task')
def trigger_specified_pages_scrapper_task(task: SpecifiedLinksScrapeTask):
    specified_links_scrapper_task.delay(task.model_dump(mode='json'))


@app.post('/uploaded-file')
def trigger_uploaded_file_task(task: UploadedFileTask):
    links = []
    for url in task.urls:
        for download in task.download_files:
            if download.path == url.path:
                if download.download_url is None:
                    continue

                url.url = download.download_url
                links.append(LinkToScrape(**url.model_dump()))
    task_to_run = SpecifiedLinksScrapeTask(**{
        **task.model_dump(), 'urls': links
    })
    uploaded_file_task.delay(task_to_run.model_dump(mode='json'))


@app.post('/sitemap-collect-scrapper-task')
def trigger_sitemap_collect_scrapper_task(task: SitemapCollectScrapperTask):
    sitemap_collect_scrapper_task.delay(task.model_dump(mode='json'))


@app.post('/entire-source-scrapper-task')
def trigger_entire_source_scrapper_task(task: EntireSourceScrapperTask):
    entire_source_scrapped_task.delay(task.model_dump(mode='json'))


@app.post('/eesti-scrapper-task')
def trigger_eesti_scrapper_task(task: EestiScrapperTask):
    eesti_scrapper_task.delay(task.model_dump(mode='json'))

@app.post('/specified-api-files-scrapper-task')
def trigger_specified_api_files_scrapper_task(task: SpecifiedApiFilesScrapeTask):
    specified_api_files_scrapper_task.delay(task.model_dump(mode='json'))

@app.post('/generate-edited-metadata')
def generate_edited_metadata(task: EditedMetadataTask):
    response = requests.get(task.download_url)
    metadata = response.json()
    metadata['edited_at'] = str(datetime.now())
    metadata['metadata']['edited'] = True

    path = Path(task.source_file_path)
    path.mkdir(parents=True, exist_ok=True)
    metadata_file = path / 'edited.meta.json'
    with metadata_file.open('w') as f:
        json.dump(metadata, f)

    requests.post(
        f"{settings.RUUTER_INTERNAL}/ckb/pipeline/upload-file-sync",
        json={
            'source_file_path': os.path.join(task.source_file_path, 'edited.meta.json'),
        }
    )
    metadata_file.unlink()

    return "OK"