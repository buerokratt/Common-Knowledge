import contextlib
import datetime
import requests
from api.config import settings
from api.models import EntityToClean


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
        send_error(
            entity.url, 'cleaning', str(e),
            entity.source_base_id, entity.agency_base_id, entity.source_run_report_base_id
        )
