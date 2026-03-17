import datetime
import json
import os
import logging

import requests

from pathlib import Path

from itemadapter import ItemAdapter
from scrapy import Spider

from api.utils import get_path_for_task
from scrapper.utils import catch_error_process_item, catch_error_spider
from scrapper.spiders.base_spider import BaseSpider
from scrapper.items import ScrappedItem
from api.models import BaseObject


def get_path_for_scrapped_item(item: ScrappedItem, spider: BaseSpider) -> str:
    scrapper_directory = spider.settings.get('SCRAPED_DIRECTORY', '/scrapped-data')

    path = get_path_for_task(spider.task)
    source_file_path = item.source_file_id

    return os.path.join(scrapper_directory, path, source_file_path)


class CreateDirectoryPipeline:
    @catch_error_process_item
    def process_item(self, item, spider: Spider):
        if not isinstance(item, ScrappedItem):
            return item

        if not hasattr(spider, 'task'):
            return item

        path = get_path_for_scrapped_item(item, spider)
        os.makedirs(path, exist_ok=True)

        item.path = path

        return item


class MetadataPipeline:
    @catch_error_process_item
    def process_item(self, item, spider: Spider):
        if not isinstance(item, ScrappedItem):
            return item

        filename = 'source.meta.json'
        full_path = os.path.join(item.path, filename)

        with open(full_path, 'w') as f:
            json.dump(ItemAdapter(item.metadata).asdict(), f)

        item.metadata_path = full_path

        r = requests.post(
            f"{spider.settings.get('RUUTER_INTERNAL')}/ckb/pipeline/upload-file-sync",
            json={
                'source_file_path': full_path,
            }
        )
        item.metadata_path_uploaded = r.json()['response']

        return item


class FilePipeline:
    @catch_error_process_item
    def process_item(self, item, spider: Spider):
        if not isinstance(item, ScrappedItem):
            return item

        filename = f'source{item.file.extension}'
        full_path = os.path.join(item.path, filename)
        with open(full_path, 'wb') as f:
            f.write(item.file.body)

        item.file_path = full_path

        r = requests.post(
            f"{spider.settings.get('RUUTER_INTERNAL')}/ckb/pipeline/upload-file-sync",
            json={
                'source_file_path': full_path,
            }
        )
        item.file_path_uploaded = r.json()['response']

        return item


class TriggerCleaningPipeline:
    @catch_error_process_item
    def process_item(self, item, spider: Spider):
        if not hasattr(spider, 'report_id'):
            return item
        spider: BaseSpider
        if not isinstance(item, ScrappedItem):
            return item

        path = get_logs_path_for_cleaning(spider)

        requests.post(
            f"{spider.settings.get('RUUTER_INTERNAL')}/ckb/pipeline/clean-scraped-file",
            json={
                'logs_path': path,
                'file_path': item.file_path,
                'meta_data_path': item.metadata_path,
                'directory_path': item.path,
                'source_file_id': item.source_file_id,
                'url': item.metadata.source_url,
                'source_base_id': spider.task.source_id,
                'agency_base_id': spider.task.agency_id,
                'source_run_report_base_id': spider.report_id,
            }
        )

        return item


class CreateSourceFile:
    @catch_error_process_item
    def process_item(self, item, spider: Spider):
        if not hasattr(spider, 'task'):
            return item

        if not isinstance(item, ScrappedItem):
            return item

        if item.source_file_id is not None:
            return item

        spider: BaseSpider
        task: BaseObject = spider.task

        res = requests.post(f'{spider.settings.get('RUUTER_INTERNAL')}/ckb/source-file/add-scrapped-file', json={
            'agency_id': task.agency_id,
            'source_id': task.source_id,
            'url': item.metadata.source_url,
            'page_title': item.metadata.page_title,
            'original_data_hash': item.hash,
            'scraped_at': item.metadata.created_at,
            'external_id': item.metadata.external_id,
            'type': 'api_file' if spider.task.__class__.__name__ == 'EestiScrapperTask' else 'scraped_file'

        })
        item.source_file_id = res.json()['response']
        return item


class UpdateSourceFile:
    @catch_error_process_item
    def process_item(self, item, spider: Spider):
        if not isinstance(item, ScrappedItem):
            return item

        requests.post(f'{spider.settings.get('RUUTER_INTERNAL')}/ckb/source-file/update-scrapped-file', json={
            'base_id': item.source_file_id,
            'url': item.metadata.source_url,
            'page_title': item.metadata.page_title,
            'original_data_url': item.file_path_uploaded,
            'original_metadata_url': item.metadata_path_uploaded,
            'original_data_hash': item.hash,
            'scraped_at': item.metadata.created_at,
            'external_id': item.metadata.external_id
        })
        return item


class ScrappingFinishedPipeline:
    @catch_error_spider
    def close_spider(self, spider: Spider):
        if not hasattr(spider, 'task'):
            return

        spider: BaseSpider
        task: BaseObject = spider.task

        requests.post(f'{spider.settings.get('RUUTER_INTERNAL')}/ckb/agency/update-zip-dirty', json={
            'sourceId': task.source_id,
            'agencyId': task.agency_id,
        })


class SetSourceStatusRunningPipeline:
    @catch_error_spider
    def open_spider(self, spider: Spider):
        if not hasattr(spider, 'task'):
            return

        spider: BaseSpider
        task: BaseObject = spider.task

        # Skip updating source status for manual file refresh (ignore_stopping flag)
        # This prevents clearing is_stopping flag when refreshing individual files
        if hasattr(task, 'ignore_stopping') and task.ignore_stopping:
            return

        requests.post(f'{spider.settings.get('RUUTER_INTERNAL')}/ckb/source/update-status', json={
            'source_id': task.source_id,
            'status': 'running',
        })


class CreateSourceRunReportPipeline:
    @catch_error_spider
    def open_spider(self, spider: Spider):
        if not hasattr(spider, 'task'):
            return

        task: BaseObject = spider.task

        agency_name = requests.get(
            f'{spider.settings.get('RUUTER_INTERNAL')}/ckb/agency/get',
            params={'baseId': task.agency_id}
        ).json()['response'][0]['name']
        spider.logger.info(f'agency_name: {agency_name}')
        url = requests.get(
            f'{spider.settings.get('RUUTER_INTERNAL')}/ckb/source/get',
            params={'baseId': task.source_id}
        ).json()['response'][0]['url']


        report_id = requests.post(f'{spider.settings.get('RUUTER_INTERNAL')}/ckb/reports/add', json={
            'agencyBaseId': task.agency_id,
            'sourceBaseId': task.source_id,
            'agencyName': agency_name,
            'url': url,
            'scrapingStartedAt': datetime.datetime.now(datetime.UTC).isoformat(),
            'scrapingFinishedAt': None,
            'error': 0,
            'scrapingLogUrl': None,
            'cleaningLogUrl': None
        }).json()['response'][0]['baseId']

        spider.report_id = report_id


def get_logs_path_for_scraper(spider: BaseSpider):
    scrapper_directory = spider.settings.get('SCRAPED_DIRECTORY', '/scrapped-data')
    path = f'logs/scraper/{spider.report_id}.log'
    return os.path.join(scrapper_directory, path)


def get_logs_path_for_cleaning(spider: BaseSpider):
    scrapper_directory = spider.settings.get('SCRAPED_DIRECTORY', '/scrapped-data')
    path = f'logs/cleaning/{spider.report_id}.log'
    return os.path.join(scrapper_directory, path)


class InitLoggingPipeline:
    @catch_error_spider
    def open_spider(self, spider: Spider | BaseSpider):
        if not hasattr(spider, 'report_id') or spider.report_id is None:
            return

        path = get_logs_path_for_scraper(spider)
        path_obj = Path(path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        path_obj.touch(exist_ok=True)

        handler = logging.FileHandler(path)
        logging.getLogger().addHandler(handler)

        formatter = logging.Formatter(
            "[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s"
        )
        handler.setFormatter(formatter)

        cleaning_path = get_logs_path_for_cleaning(spider)
        path_obj = Path(cleaning_path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        path_obj.touch(exist_ok=True)


class UploadLogsPipeline:
    @catch_error_spider
    def close_spider(self, spider: Spider):
        if not hasattr(spider, 'report_id'):
            return

        spider: BaseSpider

        path = get_logs_path_for_scraper(spider)
        r = requests.post(
            f"{spider.settings.get('RUUTER_INTERNAL')}/ckb/pipeline/upload-file-sync",
            json={
                'source_file_path': path,
            }
        )
        scraping_log_url = r.json()['response']
        os.remove(path)


        path = get_logs_path_for_cleaning(spider)
        r = requests.post(
            f"{spider.settings.get('RUUTER_INTERNAL')}/ckb/pipeline/upload-file-sync",
            json={
                'source_file_path': path,
            }
        )
        cleaning_log_url = r.json()['response']
        os.remove(path)

        requests.post(f'{spider.settings.get('RUUTER_INTERNAL')}/ckb/reports/update', json={
            'baseId': spider.report_id,
            'scrapingFinishedAt': datetime.datetime.now(datetime.UTC).isoformat(),
            'scrapingLogUrl': scraping_log_url,
            'cleaningLogUrl': cleaning_log_url,
        })
