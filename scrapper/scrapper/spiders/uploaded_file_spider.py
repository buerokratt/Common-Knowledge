import requests
from collections.abc import AsyncIterator
from scrapy.http import Response

from scrapper.spiders.specified_pages_spider import SpecifiedPagesSpider


class UploadedFileSpider(SpecifiedPagesSpider):
    name = "uploaded_file"

    def get_meta(self) -> dict:
        """
        Override to disable Playwright for uploaded files.
        Uploaded files from S3 should use direct HTTP download.
        """
        return {}

    async def parse(
        self, response: Response, **kwargs: object
    ) -> AsyncIterator[object]:
        base_id, _ = self.get_base_id_and_hash(response.request.url)

        requests.post(
            f"{self.settings.get('RUUTER_INTERNAL')}/ckb/pipeline/delete-file-sync",
            json={
                "source_file_id": base_id,
            },
        )

        async for obj in super().parse(response, **kwargs):
            yield obj
