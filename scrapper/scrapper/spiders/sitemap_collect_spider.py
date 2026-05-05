from collections.abc import AsyncIterator
from urllib.parse import urljoin, urlparse

from scrapy import Request
from scrapy.http import Response

from api.models import SitemapCollectScrapperTask
from scrapper.items import ScrappedItem
from scrapper.spiders.base_spider import BaseSpider
from scrapper.utils import is_archive_url


class SitemapCollectSpider(BaseSpider):
    name = "sitemap_collect_spider"

    def __init__(self, name: str | None = None, **kwargs: object) -> None:
        super().__init__(name, **kwargs)
        self.visited_urls: set[str] = set()
        self.scraped_urls: set[str] = set()
        self.hashes: set[str] = set()

        task = kwargs.get("task")
        if isinstance(task, SitemapCollectScrapperTask):
            self.task = task
            self.start_urls = [task.url.unicode_string()]

        self.pure_allowed_domains = [
            self.get_pure_domain(url) for url in self.start_urls
        ]
        self.scope_roots = [
            (self._normalize_host(url), self._normalize_path(url))
            for url in self.start_urls
        ]

    def get_pure_domain(self, url: str) -> str:
        parsed_url = urlparse(url)
        netloc = parsed_url.netloc
        if netloc is None:
            return ""

        return ".".join(netloc.split(".")[-2:])

    @staticmethod
    def _normalize_host(url: str) -> str:
        host = (urlparse(url).hostname or "").lower()
        if host.startswith("www."):
            return host[4:]
        return host

    @staticmethod
    def _normalize_path(url: str) -> str:
        path = urlparse(url).path or "/"
        if path != "/":
            path = path.rstrip("/")
        return path or "/"

    @staticmethod
    def _is_path_in_scope(candidate_path: str, scope_path: str) -> bool:
        if scope_path == "/":
            return True
        return candidate_path == scope_path or candidate_path.startswith(
            f"{scope_path}/"
        )

    def is_in_scope(self, url: str) -> bool:
        candidate_host = self._normalize_host(url)
        candidate_path = self._normalize_path(url)

        for scope_host, scope_path in self.scope_roots:
            if candidate_host != scope_host:
                continue
            if self._is_path_in_scope(candidate_path, scope_path):
                return True
        return False

    async def parse(
        self, response: Response, **kwargs: object
    ) -> AsyncIterator[ScrappedItem | Request]:
        assert response.request is not None
        request = response.request
        async for item in super().parse(response, **kwargs):
            if not isinstance(item, ScrappedItem):
                yield item
                continue
            scrapped_item: ScrappedItem = item
            if (
                response.status is None
                or response.status >= 300
                or response.status < 200
            ):
                continue

            if response.url in self.scraped_urls:
                continue

            self.visited_urls.add(request.url)
            self.visited_urls.add(response.url)

            self.scraped_urls.add(request.url)
            self.scraped_urls.add(response.url)

            if not self.is_in_scope(response.url):
                continue

            if scrapped_item.metadata.file_type not in self.settings.get(
                "ALLOWED_FILETYPES"
            ):
                self.logger.info(
                    f"Skipping {scrapped_item.metadata.source_url} because file type "
                    f"is {scrapped_item.metadata.file_type} and it is not allowed"
                )
                continue

            if scrapped_item.hash in self.hashes:
                self.logger.info(
                    f"Skipping {scrapped_item.metadata.source_url} because no new content was found "
                    f"and it was already scraped"
                )
            self.hashes.add(scrapped_item.hash)

            yield scrapped_item

            if scrapped_item.metadata.file_type != ".html":
                continue

            # Use rendered HTML for link extraction if available (for SPAs)
            rendered_html = response.meta.get("rendered_html")
            if rendered_html:
                from bs4 import BeautifulSoup, Tag

                soup = BeautifulSoup(rendered_html, "lxml")
                links = [
                    a.get("href")
                    for a in soup.find_all("a", href=True)
                    if isinstance(a, Tag)
                ]
            else:
                links = response.css("a::attr(href)").getall()

            for href in links:
                if not isinstance(href, str):
                    continue
                next_url = urljoin(response.url, href)
                next_url = next_url.split("#")[0]

                # Only follow links within the crawl scope defined by is_in_scope (rooted at start_urls host/path)
                if not self.is_in_scope(next_url):
                    continue

                # Skip archive URLs
                if is_archive_url(next_url):
                    self.logger.info(f"Skipping archive URL: {next_url}")
                    continue

                if next_url not in self.visited_urls:
                    self.visited_urls.add(next_url)
                    self.logger.info(f"Schedule scrape for url: {next_url}")
                    yield Request(
                        next_url,
                        callback=self.parse,
                        errback=self.errback,
                        meta=self.get_meta(),
                        headers=self.get_headers(),
                    )
