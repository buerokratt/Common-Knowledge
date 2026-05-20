import datetime
from collections.abc import Iterator

import requests

from api.models import EntireSourceScrapperTask, LinkToScrape
from scrapper.spiders.specified_pages_spider import SpecifiedPagesSpider


class EntireSourceSpider(SpecifiedPagesSpider):
    name = "entire_source_spider"

    def __init__(self, name: str | None = None, **kwargs: object) -> None:
        super().__init__(name, **kwargs)
        # Defer initialization that depends on `self.settings` until
        # the crawler calls `from_crawler` / `_set_crawler`.
        self.url_iter = None
        self.urls = []

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super(EntireSourceSpider, cls).from_crawler(crawler, *args, **kwargs)
        task = kwargs.get("task")
        if isinstance(task, EntireSourceScrapperTask):
            spider.task = task
            spider.url_iter = spider.urls_iter_impl()
            spider.start_urls = list(spider.start_url_impl())
            spider.urls = []
        return spider

    def start_url_impl(self) -> Iterator[str]:
        yield next(self.url_iter)

    def urls_iter_impl(self) -> Iterator[str]:
        scrapped_before = datetime.datetime.now(datetime.UTC).isoformat()

        while True:
            result = requests.get(
                f"{self.settings.get('RUUTER_INTERNAL')}/ckb/source-file/get-one-source-file-to-scrape",
                params={
                    "source_id": self.task.source_id,
                    "reference_time": scrapped_before,
                },
            )
            if len(result.json()["response"]) == 0:
                return

            link = LinkToScrape(**result.json()["response"][0])

            self.urls.append(link)

            yield link.url.unicode_string()
