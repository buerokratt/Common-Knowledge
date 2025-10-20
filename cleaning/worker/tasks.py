import json
import logging
import re

import requests
from langdetect import detect, LangDetectException

from unstructured.partition.auto import partition
from unstructured.partition.html import partition_html
from bs4 import BeautifulSoup

from api.config import settings
from api.models import EntityToClean
from worker.utils import catch_error

logger = logging.getLogger(__name__)


def normalize_newlines(text: str) -> str:
    """
    Normalize excessive newlines to maximum of 2 consecutive newlines.
    Replace 3 or more consecutive newlines with exactly 2 newlines.
    """
    # Replace 3 or more newlines with exactly 2 newlines
    normalized_text = re.sub(r'\n{3,}', '\n\n', text)
    return normalized_text


def clean_html(entity: EntityToClean):
    """Clean HTML files using a multi-step approach for better content extraction."""
    soup = BeautifulSoup(open(entity.file_path.as_posix(), 'r'), 'lxml')

    # Step 1: Check if there's a <main> element and use only that
    main_element = soup.find('main')
    if main_element:
        logger.info(f'Found <main> element, using only main content for {entity.file_path.as_posix()}')
        # Remove unwanted elements from main
        for element in main_element(['script', 'style', 'nav', 'aside', 'form']):
            element.decompose()
        cleaned_text = main_element.get_text(separator='\n', strip=True)
        logger.info(f'Extracted {len(cleaned_text)} chars from <main> element for {entity.file_path.as_posix()}')
        return cleaned_text

    # Step 2: Try partition_html with skip_headers_and_footers flag
    logger.info(f'No <main> element found, trying partition_html with skip_headers_and_footers for {entity.file_path.as_posix()}')
    partitioned = partition_html(
        filename=entity.file_path.as_posix(),
        languages=settings.languages,
        skip_headers_and_footers=True
    )
    cleaned_text = '\n\n'.join([str(el) for el in partitioned])

    # Step 3: If partition_html returns empty, fallback to BeautifulSoup
    if len(partitioned) == 0:
        logger.warning(f'partition_html returned empty content, using BeautifulSoup fallback for {entity.file_path.as_posix()}')

        # Remove unwanted elements (headers, footers, nav, scripts, styles)
        for element in soup(['header', 'footer', 'nav', 'script', 'style', 'aside', 'form']):
            element.decompose()

        cleaned_text = soup.get_text(separator='\n', strip=True)
        logger.info(f'BeautifulSoup fallback extracted {len(cleaned_text)} chars for {entity.file_path.as_posix()}')

    return cleaned_text


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

        # Normalize excessive newlines (max 2 consecutive newlines)
        cleaned_text = normalize_newlines(cleaned_text)
        logger.info(f'Normalized newlines for {entity.file_path.as_posix()}')

        # Detect language from cleaned text
        detected_language = None
        if cleaned_text and len(cleaned_text.strip()) > 0:
            try:
                detected_language = detect(cleaned_text)
                print(f'Detected language: {detected_language} for {entity.file_path.as_posix()}')
            except LangDetectException as e:
                print(f'Language detection failed for {entity.file_path.as_posix()}: {e}')

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
            metadata['language'] = detected_language
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
