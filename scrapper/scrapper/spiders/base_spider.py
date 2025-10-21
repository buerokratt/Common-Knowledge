import mimetypes
import hashlib
import contextlib

from typing import Any, AsyncIterator

import requests
from bs4 import BeautifulSoup
from playwright.async_api import Page

from fake_useragent import UserAgent
from scrapy import Spider, Request
from scrapy.exceptions import CloseSpider
from scrapy.http import Response
from twisted.python.failure import Failure

from api.models import BaseObject
from scrapper.items import FileItem, MetadataItem, Metadata, ScrappedItem
from scrapper.utils import send_error, is_archive_url


class BaseSpider(Spider):
    task: BaseObject
    handle_httpstatus_list = [*range(600)]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.ua = UserAgent(platforms='desktop')
        self.report_id = None

        if isinstance(kwargs.get('task'), BaseObject):
            self.task = kwargs['task']

    def check_source_is_stopping(self):
        try:
            is_stopping = requests.get(
                f'{self.settings.get('RUUTER_INTERNAL')}/ckb/source/get',
                params={'baseId': self.task.source_id}
            ).json()['response'][0]['isStopping']
        except Exception:
            raise CloseSpider('source not found')
        if is_stopping:
            raise CloseSpider('source is stopping')

    def get_meta(self):
        return {
            'playwright': True,
            'playwright_include_page': True,
            'playwright_page_goto_kwargs': {
                'timeout': 30_000,
                'wait_until': 'load',
            },
            "playwright_context_kwargs": {
                "ignore_https_errors": True,
            },
        }

    def get_headers(self):
        return {
            "User-Agent": self.ua.random,
        }

    async def start(self) -> AsyncIterator[Any]:
        for url in self.start_urls:
            yield Request(
                url, dont_filter=True, meta=self.get_meta(), headers=self.get_headers(),  errback=self.errback
            )

    def guess_file_extension(self, content_type: str) -> str:
        pure_content_type = content_type.split(';')[0]
        guessed_extension = mimetypes.guess_extension(pure_content_type)
        return guessed_extension

    @contextlib.asynccontextmanager
    async def close_page(self, response: Response) -> AsyncIterator[Page]:
        page: Page = response.meta["playwright_page"]
        try:
            self.logger.info(f'Page returned {response.url}')
            yield page
        finally:
            await page.close()
            await page.context.close()
            self.logger.info(f'Page closed {response.url}')

    def log_error_to_source_run_page(self, request, error_type: str, error_message: str):
        if isinstance(request, str):
            url = request
        else:
            url = request.url

        # Log to file as well as database
        self.logger.error(f"[{error_type}] {url}: {error_message}")

        send_error(
            self.settings.get('RUUTER_INTERNAL'),
            url, error_type, error_message,
            self.task.source_id, self.task.agency_id, self.report_id
        )


    async def errback(self, failure: Failure):
        if not hasattr(failure, 'request'):
            return

        self.log_error_to_source_run_page(failure.request, error_type='scrapper', error_message=str(failure))
        page = failure.request.meta.get("playwright_page")
        if page is not None:
            await page.close()

    async def parse(self, response: Response, **kwargs):
        self.check_source_is_stopping()

        # Check if URL is an archive page and skip if it is
        if is_archive_url(response.url):
            self.logger.info(f'Skipping archive URL: {response.url}')
            return

        file_extension = self.guess_file_extension(
            response.headers.get(b'Content-Type', 'text/html').decode('utf-8')
        )

        # Check if Playwright page is available (might not be if direct HTTP download was used)
        playwright_page = response.meta.get("playwright_page")
        if playwright_page:
            # Use Playwright page for title extraction
            async with self.close_page(response) as page:
                page: Page
                if file_extension == '.html':
                    title = await page.title()
                else:
                    title = response.url
        else:
            # Direct HTTP download (no Playwright page available)
            if file_extension == '.html':
                # Extract title from HTML using BeautifulSoup
                soup = BeautifulSoup(response.body, 'lxml')
                title_tag = soup.find('title')
                title = title_tag.get_text() if title_tag else response.url
            else:
                title = response.url

        if file_extension == '.html':
            soup = BeautifulSoup(response.body, 'lxml')
            text = soup.get_text()
            hashed = hashlib.sha1(text.encode()).hexdigest()
        else:
            hashed = hashlib.sha1(response.body).hexdigest()

        file_item = FileItem(body=response.body, source_url=response.url, extension=file_extension)

        metadata_item = MetadataItem(
            file_type=file_extension, metadata=Metadata(), source_url=response.url, page_title=title
        )

        scrapped_item = ScrappedItem(file=file_item, metadata=metadata_item, hash=hashed)
        yield scrapped_item
