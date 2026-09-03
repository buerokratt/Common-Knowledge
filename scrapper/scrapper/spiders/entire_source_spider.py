import datetime
from collections.abc import Iterator
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from scrapy.crawler import Crawler  # type: ignore[import-untyped]

from api.models import EntireSourceScrapperTask, LinkToScrape
from scrapper.spiders.specified_pages_spider import SpecifiedPagesSpider


class EntireSourceSpider(SpecifiedPagesSpider):
    name = "entire_source_spider"

    def __init__(self, name: str | None = None, **kwargs: object) -> None:
        super().__init__(name, **kwargs)
        task = kwargs.get("task")
        if isinstance(task, EntireSourceScrapperTask):
            self.task = task
            # Defer any initialization that requires Scrapy settings until
            # after the crawler has been attached (see from_crawler below).
            self.url_iter = None
            self.start_urls = []
            self.urls = []

    @classmethod
    def from_crawler(
        cls, crawler: "Crawler", *args: object, **kwargs: object
    ) -> "EntireSourceSpider":
        """Create spider and perform crawler-dependent initialization.

        Scrapy calls the classmethod `from_crawler` which attaches the
        crawler (and therefore `settings`) to the spider *after* the
        instance is constructed. Any initialization that needs access to
        `self.settings` must happen here, not in `__init__`.
        """
        spider = super().from_crawler(crawler, *args, **kwargs)
        task = kwargs.get("task")
        if isinstance(task, EntireSourceScrapperTask):
            spider.url_iter = spider.urls_iter_impl()
            # start_url_impl consumes the iterator once to produce the
            # initial start URL. If the iterator is empty, it returns an
            # empty iterator (so start_urls becomes []).
            spider.start_urls = list(spider.start_url_impl())
        return spider

    def start_url_impl(self) -> Iterator[str]:
        if self.url_iter is None:
            return iter(())

        try:
            first_url = next(self.url_iter)
        except StopIteration:
            return iter(())

        return iter((first_url,))

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
