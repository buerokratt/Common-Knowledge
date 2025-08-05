import requests
import hashlib
import datetime
from typing import Dict, List, Optional
from scrapy.http import Response

from scrapper.spiders.specified_pages_spider import SpecifiedPagesSpider
from scrapper.items import FileItem, MetadataItem, Metadata, ScrappedItem
from api.models import SpecifiedApiFilesScrapeTask


class SpecifiedApiFilesSpider(SpecifiedPagesSpider):
    name = 'specified_api_files_spider'
    
    custom_settings = {
        'ROBOTSTXT_OBEY': False,
        'DOWNLOAD_DELAY': 0,
    }

    def __init__(self, *args, **kwargs):
        # Set API config first, before calling super()
        self.base_url = "https://www.eesti.ee"
        self.article_api = f"{self.base_url}/api/article/v2"
        
        super().__init__(*args, **kwargs)
        
        if isinstance(kwargs.get('task'), SpecifiedApiFilesScrapeTask):
            self.task: SpecifiedApiFilesScrapeTask = kwargs.get('task')
            # Convert api_files to the format SpecifiedPagesSpider expects
            self.urls = self.task.api_files
            if self.urls:
                # Set start_urls like SpecifiedPagesSpider does
                self.start_urls = [self.construct_api_url(self.urls[0].externalId)]
                # Create iterator for remaining URLs
                remaining_urls = [self.construct_api_url(api_file.externalId) for api_file in self.urls[1:]]
                self.url_iter = iter(remaining_urls)

    def construct_api_url(self, external_id: str) -> str:
        """Construct API URL from external_id like EestiSpider does"""
        return f"{self.article_api}/{external_id}"

    def get_base_id_and_hash(self, url: str):
        """
        Same as SpecifiedPagesSpider but matches by externalId instead of URL
        """
        base_id = None
        hashed = None
        
        # Extract external_id from API URL
        external_id = url.split('/')[-1]
        
        for api_file in self.urls:
            if api_file.externalId == external_id:
                base_id = api_file.id
                hashed = api_file.hash
                break
                
        return base_id, hashed

    def fetch_article_from_api(self, external_id: str) -> Optional[Dict]:
        """Fetch article content from Eesti.ee API (same as EestiSpider)"""
        try:
            url = f"{self.article_api}/{external_id}"
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            self.logger.error(f"Error fetching article {external_id}: {e}")
            return None

    async def parse(self, response: Response, **kwargs):
        """
        Override parse to fetch content from API instead of scraping HTML.
        Same hash comparison logic as SpecifiedPagesSpider.
        """
        # Get database info for hash comparison (same as SpecifiedPagesSpider)
        base_id, hashed = self.get_base_id_and_hash(response.request.url)

        # Extract external_id from API URL
        external_id = response.request.url.split('/')[-1]
        
        # Fetch article content from API instead of parsing HTML
        article_data = self.fetch_article_from_api(external_id)
        if not article_data:
            self.logger.error(f"Failed to fetch article {external_id} from API")
            if base_id:
                requests.post(
                    f"{self.settings.get('RUUTER_INTERNAL')}/ckb/source-file/update-scrapped-file-stop-scrapping",
                    json={'base_id': base_id, 'status': 'failed'},
                )
            return

        # Create the HTML content (same as EestiSpider)
        title = article_data.get('title', '')
        description = article_data.get('description', '')
        content = article_data.get('content', '')
        
        html_content = f"""<!DOCTYPE html>
<html lang="et">
<head>
    <meta charset="UTF-8">
    <title>{title}</title>
    <meta name="description" content="{description}">
</head>
<body>
    <h1>{title}</h1>
    {content}
</body>
</html>"""
        
        body_bytes = html_content.encode('utf-8')
        
        # Calculate hash from the HTML content (same approach as BaseSpider)
        new_hash = hashlib.sha1(body_bytes).hexdigest()

        # Same hash comparison logic as SpecifiedPagesSpider
        if hashed is not None and new_hash == hashed:
            self.logger.info(
                f'Skipping article {external_id} because hash did not change and it contains same data'
            )
            requests.post(
                f"{self.settings.get('RUUTER_INTERNAL')}/ckb/source-file/update-scrapped-file-stop-scrapping",
                json={'base_id': base_id, 'status': 'finished'},
            )
            return

        # Check if file type is allowed (same as SpecifiedPagesSpider)
        file_extension = '.html'
        if file_extension not in self.settings.get('ALLOWED_FILETYPES'):
            self.logger.info(
                f'Skipping article {external_id} because file type '
                f'is {file_extension} and it is not allowed')
            requests.post(
                f"{self.settings.get('RUUTER_INTERNAL')}/ckb/source-file/update-scrapped-file-stop-scrapping",
                json={'base_id': base_id, 'status': 'failed'},
            )
            return

        # Create ScrappedItem (same structure as BaseSpider)
        file_item = FileItem(
            body=body_bytes,
            source_url=response.request.url,
            extension=file_extension
        )
        
        metadata_item = MetadataItem(
            file_type=file_extension,
            metadata=Metadata(),
            source_url=response.request.url,
            page_title=title,
            external_id=external_id
        )
        
        scrapped_item = ScrappedItem(
            file=file_item,
            metadata=metadata_item,
            hash=new_hash,
            source_file_id=base_id
        )

        yield scrapped_item