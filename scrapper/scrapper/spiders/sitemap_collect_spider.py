from functools import cache
from urllib.parse import urljoin, urlparse

from scrapy import Request
from scrapy.http import Response

from api.models import SitemapCollectScrapperTask
from scrapper.spiders.base_spider import BaseSpider
from scrapper.utils import is_archive_url


class SitemapCollectSpider(BaseSpider):
    name = 'sitemap_collect_spider'
    # start_urls = ['https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf']
    # start_urls = ['https://calibre-ebook.com/downloads/demos/demo.docx']
    start_urls = [
        # "https://www.terviseamet.ee",
        # "https://www.tervisekassa.ee",
        # "https://www.ravimiamet.ee",
        # "https://www.sm.ee",
        # "https://www.sotsiaalkindlustusamet.ee",
        # "https://www.tootukassa.ee",
        # "https://elron.ee/",
        # "https://www.transpordiamet.ee",
        # "https://www.airport.ee",
        # "https://www.ts.ee",??????????????
        # "https://www.lkf.ee/et",
        #  "https://www.fi.ee",
        #  "https://www.eestipank.ee",
        #  "https://www.kredex.ee",
        #  "https://www.emta.ee",
        #  "https://www.fin.ee",
        #     "https://www.ti.ee",
        #     "https://www.eakl.ee",
        #     "https://www.tooelu.ee",
        #     "https://www.minukarjaar.ee",
        #     "https://www.just.ee",
        #     "https://www.notar.ee",
        #     "https://www.kohus.ee",
        #     "https://www.kpkoda.ee",
        #     "https://www.riigiteataja.ee",
        #     "https://www.korruptsioon.ee",
        #     "https://www.maaamet.ee",
        #     "https://www.tallinn.ee/et/ehitus",
        #     "https://www.hm.ee",
        #     "https://www.harno.ee",
        #     "https://www.politsei.ee",
        #     "https://www.valimised.ee",
        #     "https://integratsioon.ee/",
        #     "https://www.siseministeerium",
        #     "https://www.tja.ee",
        #     "https://www.kaitseministeerium.ee",
        #     "https://www.mil.ee",
        #     "https://www.kaitseliit.ee",
        #     "https://www.kriis.ee",
        #     "https://www.rescue.ee",
        #     "https://www.kapo.ee",
        #     "https://www.kul.ee",
        #     "https://www.kik.ee",
        #     "https://www.envir.ee",
        #     "https://www.keskkonnaagentuur.ee",
        #     "https://www.keskkonnaamet.ee",
        #     "https://www.pria.ee",
        #     "https://www.agri.ee",
        #     "https://www.peaasi.ee",
        #     "https://www.lasteabi.ee",
        #     "https://www.vaimnetervis.ee",
        #     "https://koolirahu.lastekaitseliit.ee/et/",
        #     "https://www.itvaatlik.ee",
        #     "https://www.riigikogu.ee",
        #     "https://www.muinsuskaitseamet.ee",
        #     "https://www.eesti.ee",
        #     "https://www.epa.ee",
    ]

    # custom_settings = {
    #     'ROBOTSTXT_OBEY': False
    # }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.visited_urls = set()
        self.scraped_urls = set()
        self.hashes = set()

        if isinstance(kwargs.get('task'), SitemapCollectScrapperTask):
            self.task: SitemapCollectScrapperTask = kwargs.get('task')
            self.start_urls = [self.task.url.unicode_string()]

    def get_pure_domain(self, url: str) -> str:
        parsed_url = urlparse(url)
        netloc = parsed_url.netloc
        if netloc is None:
            return ''

        return '.'.join(netloc.split('.')[-2:])

    @property
    @cache
    def pure_allowed_domains(self):
        pure_domains = [self.get_pure_domain(url) for url in self.start_urls]
        return pure_domains

    async def parse(self, response: Response, **kwargs):
        async for scrapped_item in super().parse(response, **kwargs):
            if response.status is None or response.status >= 300 or response.status < 200:
                continue

            if response.url in self.scraped_urls:
                continue

            self.visited_urls.add(response.request.url)
            self.visited_urls.add(response.url)

            self.scraped_urls.add(response.request.url)
            self.scraped_urls.add(response.url)

            if self.get_pure_domain(response.url) not in self.pure_allowed_domains:
                continue

            if scrapped_item.metadata.file_type not in self.settings.get('ALLOWED_FILETYPES'):
                self.logger.info(
                    f'Skipping {scrapped_item.metadata.source_url} because file type '
                    f'is {scrapped_item.metadata.file_type} and it is not allowed')
                continue

            if scrapped_item.hash in self.hashes:
                self.logger.info(
                    f'Skipping {scrapped_item.metadata.source_url} because no new content was found '
                    f'and it was already scraped'
                )
            self.hashes.add(scrapped_item.hash)

            yield scrapped_item

            if scrapped_item.metadata.file_type != '.html':
                continue

            for href in response.css("a::attr(href)").getall():
                next_url = urljoin(response.url, href)
                next_url = next_url.split('#')[0]

                # Only follow links within allowed_domains
                if self.get_pure_domain(next_url) not in self.pure_allowed_domains:
                    continue

                # Skip archive URLs
                if is_archive_url(next_url):
                    self.logger.info(f'Skipping archive URL: {next_url}')
                    continue

                if next_url not in self.visited_urls:
                    self.visited_urls.add(next_url)
                    self.logger.info(f'Schedule scrape for url: {next_url}')
                    yield Request(
                        next_url, callback=self.parse, errback=self.errback,
                        meta=self.get_meta(), headers=self.get_headers()
                    )
