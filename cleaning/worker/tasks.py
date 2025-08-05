import json
import logging

import requests

from unstructured.partition.auto import partition
from bs4 import BeautifulSoup

from api.config import settings
from api.models import EntityToClean
from worker.utils import catch_error

logger = logging.getLogger(__name__)


def clean_html(entity: EntityToClean):
    with entity.file_path.open('r') as f:
        soup = BeautifulSoup(f.read(), 'lxml')

    return soup.get_text()


def clean_any_file(entity: EntityToClean):
    partitioned = partition(filename=entity.file_path.as_posix(), languages=settings.languages)
    cleaned_text = '\n\n'.join([str(el) for el in partitioned])
    return cleaned_text


def set_up_logging(entity: EntityToClean):
    handler = logging.FileHandler(entity.logs_path.as_posix())

    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s"
    )
    handler.setFormatter(formatter)


def clean_file_task(entity: EntityToClean):
    with catch_error(entity):
        set_up_logging(entity)
        logger.info(f'Cleaning file {entity.file_path.as_posix()}')
        with entity.meta_data_path.open('r') as f:
            metadata = json.load(f)

        logger.info(f"loaded metadata for {entity.file_path.as_posix()}")

        if metadata['file_type'] == '.html':
            cleaned_text = clean_html(entity)
            logger.info(f'Cleaned as html for {entity.file_path.as_posix()}')
        else:
            cleaned_text = clean_any_file(entity)
            logger.info(f'Cleaned as unstructured file for {entity.file_path.as_posix()}')

        cleaned_text_filename = entity.directory_path / 'cleaned.txt'

        with cleaned_text_filename.open("w") as f:
            f.write(cleaned_text)


        r = requests.post(
            f"{settings.ruuter_internal}/ckb/pipeline/upload-file-sync",
            json={
                'source_file_path': cleaned_text_filename.as_posix(),
            }
        )
        uploaded_cleaned_text_url = r.json()['response']

        logger.info(f'Saved cleaned text for {entity.file_path.as_posix()}')

        cleaned_metadata_filename = entity.directory_path / "cleaned.meta.json"
        with cleaned_metadata_filename.open("w") as f:
            metadata['metadata']['cleaned'] = True
            json.dump(metadata, f)

        r = requests.post(
            f"{settings.ruuter_internal}/ckb/pipeline/upload-file-sync",
            json={
                'source_file_path': cleaned_metadata_filename.as_posix(),
            }
        )
        uploaded_cleaned_metadata_url = r.json()['response']

        logger.info(f'Saved cleaned metadata for {entity.file_path.as_posix()}')

        requests.post(
            f"{settings.ruuter_internal}/ckb/source-file/update-cleaned-file",
            json={
                'base_id': entity.source_file_id,
                'cleaned_data_url': uploaded_cleaned_text_url,
                'cleaned_metadata_url': uploaded_cleaned_metadata_url,
            }
        )
