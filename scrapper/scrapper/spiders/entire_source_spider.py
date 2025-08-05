import datetime
import requests

from api.models import EntireSourceScrapperTask, LinkToScrape
from scrapper.spiders.specified_pages_spider import SpecifiedPagesSpider


class EntireSourceSpider(SpecifiedPagesSpider):

    name = 'entire_source_spider'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if isinstance(kwargs.get('task'), EntireSourceScrapperTask):
            self.task: EntireSourceScrapperTask = kwargs.get('task')
            self.url_iter = self.urls_iter_impl()
            self.start_urls = self.start_url_impl()
            self.urls = []

    def start_url_impl(self):
        yield next(self.url_iter)

    def urls_iter_impl(self):
        scrapped_before = datetime.datetime.now(datetime.UTC).isoformat()

        while True:
            result = requests.get(
                f"{self.settings.get('RUUTER_INTERNAL')}/ckb/source-file/get-one-source-file-to-scrape",
                params={
                    'source_id': self.task.source_id,
                    'reference_time': scrapped_before,
                }
            )
            if len(result.json()['response']) == 0:
                return

            link = LinkToScrape(**result.json()['response'][0])

            self.urls.append(link)

            yield link.url.unicode_string()
