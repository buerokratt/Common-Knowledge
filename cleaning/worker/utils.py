import contextlib
import datetime
import logging
import shutil

import requests

from api.config import settings
from api.models import EntityToClean

logger = logging.getLogger(__name__)


def send_error(
    url: str, error_type: str, error_message: str,
    source_base_id: str, agency_base_id: str, source_run_report_base_id: str
):
    scraped_at = datetime.datetime.now(datetime.UTC).isoformat()
    try:
        requests.post(
            f"{settings.ruuter_internal}/ckb/reports/logs/add",
            json={
                "url": url,
                "scraped_at": scraped_at,
                "error_type": error_type,
                "error_message": error_message,
                "source_base_id": source_base_id,
                "agency_base_id": agency_base_id,
                "source_run_report_base_id": source_run_report_base_id,
            },
            timeout=10,
        )
    except requests.RequestException as e:
        # Log locally if the error report itself fails — don't raise so cleanup still runs
        logger.error(f"[cleaning] failed to send error report: {e}")


@contextlib.contextmanager
def catch_error(entity: EntityToClean):
    try:
        yield
    except Exception as e:
        logger.error(f"[cleaning] {entity.url}: {e}")
        send_error(
            entity.url,
            "cleaning",
            str(e),
            entity.source_file_id,
            entity.agency_base_id,
            entity.source_run_report_base_id,
        )


def cleanup_directory(entity: EntityToClean):
    """
    Called explicitly by tasks.py after successful upload confirmation.
    Keeping this separate from catch_error means a failed upload does NOT
    delete the working directory — files remain available for retry/inspection.
    """
    try:
        if entity.directory_path.exists():
            shutil.rmtree(entity.directory_path)
            logger.info(f"Cleaned up directory: {entity.directory_path}")
    except Exception as e:
        logger.error(f"Failed to cleanup directory: {e}")