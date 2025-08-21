import contextlib
import datetime
import requests
from api.config import settings
from api.models import EntityToClean
import logging
import shutil

logger = logging.getLogger(__name__)


def send_error(
    url: str, error_type: str, error_message: str,
    source_base_id: str, agency_base_id: str, source_run_report_base_id: str
):
    scraped_at = datetime.datetime.now(datetime.UTC).isoformat()
    requests.post(
        f"{settings.ruuter_internal}/ckb/reports/logs/add", json={
            'url': url,
            'scraped_at': scraped_at,
            'error_type': error_type,
            'error_message': error_message,
            'source_base_id': source_base_id,
            'agency_base_id': agency_base_id,
            'source_run_report_base_id': source_run_report_base_id,
        })


@contextlib.contextmanager
def catch_error(entity: EntityToClean):
    try:
        yield
    except Exception as e:
        # Log to file as well as database
        logger.error(f"[cleaning] {entity.url}: {str(e)}")
        
        send_error(
            entity.url, 'cleaning', str(e),
            entity.source_base_id, entity.agency_base_id, entity.source_run_report_base_id
        )
    finally:
        # Always clean up the directory, whether success or failure
        try:
            if entity.directory_path.exists():
                shutil.rmtree(entity.directory_path)
                logger.info(f'Cleaned up directory: {entity.directory_path}')
        except Exception as cleanup_error:
            logger.error(f'Failed to cleanup directory: {cleanup_error}')

