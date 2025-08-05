import contextlib
import datetime
import functools
import typing
import requests

from scrapper.items import ScrappedItem

if typing.TYPE_CHECKING:
    from scrapper.spiders.base_spider import BaseSpider
else:
    BaseSpider = object


def send_error(
    ruuter_internal: str,
    url: str, error_type: str, error_message: str,
    source_base_id: str, agency_base_id: str, source_run_report_base_id: str
):
    scraped_at = datetime.datetime.now(datetime.UTC).isoformat()
    requests.post(
        f"{ruuter_internal}/ckb/reports/logs/add", json={
            'url': url,
            'scraped_at': scraped_at,
            'error_type': error_type,
            'error_message': error_message,
            'source_base_id': source_base_id,
            'agency_base_id': agency_base_id,
            'source_run_report_base_id': source_run_report_base_id,
        })


@contextlib.contextmanager
def catch_error(url, spider: BaseSpider):
    try:
        yield
    except Exception as e:
        spider.log_error_to_source_run_page(url, 'scrapper', str(e))


def catch_error_process_item(f):
    @functools.wraps(f)
    def process_item(self, item, spider: BaseSpider):
        if not isinstance(spider, BaseSpider):
            return item

        if not isinstance(item, ScrappedItem):
            return item

        r = None
        with catch_error(item.metadata.source_url, spider):
            r = f(self, item, spider)
        return r
    return process_item


def catch_error_spider(f):
    @functools.wraps(f)
    def decorator(self, spider: BaseSpider):
        if not isinstance(spider, BaseSpider):
            return

        r = None
        with catch_error('internal', spider):
            r = f(self, spider)
        return r

    return decorator
