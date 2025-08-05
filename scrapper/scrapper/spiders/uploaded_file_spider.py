import requests
from scrapy.http import Response

from scrapper.spiders.specified_pages_spider import SpecifiedPagesSpider


class UploadedFileSpider(SpecifiedPagesSpider):
    name = 'uploaded_file'

    async def parse(self, response: Response, **kwargs):
        base_id, _ = self.get_base_id_and_hash(response.request.url)

        requests.post(
            f"{self.settings.get('RUUTER_INTERNAL')}/ckb/pipeline/delete-file-sync",
            json={
                'source_file_id': base_id,
            }
        )

        # async yield from super().parse(response, **kwargs)
        async for obj in super().parse(response, **kwargs):
            yield obj