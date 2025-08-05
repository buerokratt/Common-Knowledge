from contextlib import suppress

import requests
from scrapy import Request
from scrapy.http import Response

from scrapper.spiders.base_spider import BaseSpider
from api.models import SpecifiedLinksScrapeTask


class SpecifiedPagesSpider(BaseSpider):
    name = 'specified_pages_spider'
    custom_settings = {
        'ROBOTSTXT_OBEY': False
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if isinstance(kwargs.get('task'), SpecifiedLinksScrapeTask):
            self.task: SpecifiedLinksScrapeTask = kwargs.get('task')
            self.start_urls = [self.task.urls[0].url.unicode_string()]
            self.url_iter = iter([url.url.unicode_string() for url in self.task.urls[1:]])
            self.urls = self.task.urls

    def get_base_id_and_hash(self, url: str):
        base_id = None
        hashed = None
        self.logger.info(f'number of urls: {len(self.urls)}')
        for source_file in self.urls:
            if source_file.url.unicode_string() == url:
                base_id = source_file.id
                hashed = source_file.hash
        return base_id, hashed

    async def parse(self, response: Response, **kwargs):
        base_id, hashed = self.get_base_id_and_hash(response.request.url)

        async for obj in super().parse(response, **kwargs):
            if response.status is None or response.status >= 300 or response.status < 200:
                break

            obj.source_file_id = base_id

            if hashed is not None and obj.hash == hashed:
                self.logger.info(
                    f'Skipping {obj.metadata.source_url} because hash did not changed and it contains same data'
                )
                requests.post(
                    f"{self.settings.get('RUUTER_INTERNAL')}/ckb/source-file/update-scrapped-file-stop-scrapping",
                    json={'base_id': base_id, 'status': 'finished'},
                )
                continue

            if obj.metadata.file_type not in self.settings.get('ALLOWED_FILETYPES'):
                self.logger.info(
                    f'Skipping {obj.url} because file type '
                    f'is {obj.metadata.file_type} and it is not allowed')
                requests.post(
                    f"{self.settings.get('RUUTER_INTERNAL')}/ckb/source-file/update-scrapped-file-stop-scrapping",
                    json={'base_id': base_id, 'status': 'failed'},
                )
                self.log_error_to_source_run_page(
                    response.request, 'content',
                    f'new content does not match allowed file type (got {obj.metadata.file_type})'
                )
                continue

            yield obj


        if response.status is None or response.status >= 300 or response.status < 200:
            self.logger.info(
                f'{response.request.url} Not found with status code {response.status}'
            )
            requests.post(
                f"{self.settings.get('RUUTER_INTERNAL')}/ckb/source-file/update-scrapped-file-stop-scrapping",
                json={'base_id': base_id, 'status': 'not_found'},
            )
            self.log_error_to_source_run_page(
                response.request, 'http',
                f'invalid status code: {response.status}'
            )

        with suppress(StopIteration):
            yield Request(
                next(self.url_iter), callback=self.parse, errback=self.errback,
                meta=self.get_meta(), headers=self.get_headers()
            )
