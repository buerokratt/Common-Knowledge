import requests
import time
import json
import hashlib
import datetime
from typing import Dict, List, Optional
from scrapy.http import Response
from scrapy import Request

from scrapper.spiders.base_spider import BaseSpider
from scrapper.items import FileItem, MetadataItem, Metadata, ScrappedItem
from api.models import EestiScrapperTask


class EestiSpider(BaseSpider):
    name = 'eesti_spider'
    
    custom_settings = {
        'ROBOTSTXT_OBEY': False,
        'DOWNLOAD_DELAY': 0,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        if isinstance(kwargs.get('task'), EestiScrapperTask):
            self.task: EestiScrapperTask = kwargs.get('task')
        
        self.base_url = "https://www.eesti.ee"
        self.menu_api = f"{self.base_url}/api/menu/et"
        self.article_api = f"{self.base_url}/api/article/v2"
        self.start_urls = []

    def fetch_menu(self) -> Optional[Dict]:
        """Fetch the menu structure from the API"""
        try:
            self.logger.info("Fetching Eesti.ee menu structure...")
            response = requests.get(self.menu_api, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            self.logger.error(f"Error fetching menu: {e}")
            return None

    def extract_article_ids(self, menu_data: Dict) -> List[Dict]:
        """Recursively extract all arvaArticleId values from the menu structure"""
        article_entries = []
        
        def recursive_search(item):
            if isinstance(item, dict):
                if 'arvaArticleId' in item:
                    entry = {
                        'id': item['arvaArticleId'],
                        'href': item.get('href', ''),
                        'title': item.get('title', ''),
                        'pageTitle': item.get('pageTitle', '')
                    }
                    article_entries.append(entry)
                
                for key, value in item.items():
                    if key == 'children' and isinstance(value, list):
                        for child in value:
                            recursive_search(child)
                    elif isinstance(value, (dict, list)):
                        recursive_search(value)
            
            elif isinstance(item, list):
                for sub_item in item:
                    recursive_search(sub_item)
        
        if 'data' in menu_data:
            recursive_search(menu_data['data'])
        else:
            recursive_search(menu_data)
        
        return article_entries

    def get_existing_articles_from_db(self) -> Dict[str, Dict]:
        """Get existing Eesti articles from database using the same while loop pattern"""
        existing_articles = {}
        scrapped_before = datetime.datetime.now(datetime.UTC).isoformat()
        
        try:
            self.logger.info("Fetching existing articles from database...")
            
            while True:
                # Use same endpoint as EntireSourceSpider
                result = requests.get(
                    f"{self.settings.get('RUUTER_INTERNAL')}/ckb/source-file/get-one-source-file-to-scrape",
                    params={
                        'source_id': self.task.source_id,
                        'reference_time': scrapped_before,
                    },
                    timeout=10
                )
                
                if result.status_code != 200:
                    break
                    
                response_data = result.json().get('response', [])
                if len(response_data) == 0:
                    break
                
                # Extract article info
                article_info = response_data[0]
                external_id = article_info.get('externalId')
                self.logger.info(f"external_id {external_id}")
                if external_id:
                    existing_articles[str(external_id)] = article_info
                    
                # Continue until no more articles
                
            self.logger.info(f"Found {len(existing_articles)} existing articles in database")
            return existing_articles
            
        except Exception as e:
            self.logger.info(f"Error fetching existing articles (normal for first run): {e}")
            return {}

    def fetch_article_from_api(self, external_id: str) -> Optional[Dict]:
        """Fetch article content from Eesti.ee API"""
        try:
            url = f"{self.article_api}/{external_id}"
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            self.logger.error(f"Error fetching article {external_id}: {e}")
            return None

    def calculate_content_hash(self, article_data: Dict) -> str:
        """Calculate hash from article content"""
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
        return hashlib.sha1(body_bytes).hexdigest()

    def should_process_article(self, article_entry: Dict, article_data: Dict, existing_articles: Dict[str, Dict]) -> bool:
        """Check if article should be processed (new or changed)"""
        external_id = str(article_entry['id'])
        
        # New article - always process
        if external_id not in existing_articles:
            self.logger.info(f"Article {external_id} is new - will process")
            return True
        
        # Existing article - check if content changed
        new_hash = self.calculate_content_hash(article_data)
        existing_hash = existing_articles[external_id].get('hash', '')
        
        if new_hash != existing_hash:
            self.logger.info(f"Article {external_id} content changed - will process")
            return True
        else:
            self.logger.info(f"Article {external_id} unchanged - skipping")
            requests.post(
                f"{self.settings.get('RUUTER_INTERNAL')}/ckb/source-file/update-scrapped-file-stop-scrapping",
                json={'base_id': existing_articles[external_id].get('id'), 'status': 'finished'}
            )
            return False

    def add_or_update_article_in_db(self, article_entry: Dict, existing_articles: Dict[str, Dict]) -> Optional[str]:
        """Add new article or get existing article ID from database"""
        external_id = str(article_entry['id'])
        
        # If article already exists, return its source_file_id
        if external_id in existing_articles:
            return existing_articles[external_id].get('id')
        
        # Add new article to database
        try:
            title = article_entry.get('title', '')
            href = article_entry.get('href', '')
            article_url = f"{self.base_url}{href}" if href else f"{self.base_url}/article/{external_id}"
            
            response = requests.post(
                f"{self.settings.get('RUUTER_INTERNAL')}/ckb/source-file/add-scrapped-file",
                json={
                    'agency_id': self.task.agency_id,
                    'source_id': self.task.source_id,
                    'url': article_url,
                    'page_title': title,
                    'original_data_hash': '',  # Will be updated after processing
                    'scraped_at': datetime.datetime.now(datetime.UTC).isoformat(),
                    'external_id': external_id,
                    'type': 'api_file'
                },
                timeout=10
            )
            
            if response.status_code == 200:
                source_file_id = response.json()['response']
                self.logger.info(f"Added new article {external_id} to database with ID {source_file_id}")
                return source_file_id
            else:
                self.logger.error(f"Failed to add article {external_id}: HTTP {response.status_code}")
                return None
                
        except Exception as e:
            self.logger.error(f"Error adding article {external_id} to database: {e}")
            return None

    def create_scrapped_item(self, article_data: Dict, article_entry: Dict, source_file_id: str) -> ScrappedItem:
        """Create a ScrappedItem from API article data"""
        external_id = str(article_entry['id'])
        title = article_data.get('title', article_entry.get('title', ''))
        description = article_data.get('description', '')
        content = article_data.get('content', '')
        href = article_entry.get('href', '')
        
        # Create article URL
        article_url = f"{self.base_url}{href}" if href else f"{self.base_url}/article/{external_id}"
        
        # Create full HTML content
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
        
        # Convert to bytes
        body_bytes = html_content.encode('utf-8')
        
        # Calculate hash
        content_hash = hashlib.sha1(body_bytes).hexdigest()
        
        # Create file item
        file_item = FileItem(
            body=body_bytes,
            source_url=article_url,
            extension='.html'
        )
        
        # Create metadata item
        metadata_item = MetadataItem(
            file_type='.html',
            metadata=Metadata(),
            source_url=article_url,
            page_title=title,
            external_id=external_id
        )
        
        # Create scrapped item
        scrapped_item = ScrappedItem(
            file=file_item,
            metadata=metadata_item,
            hash=content_hash,
            source_file_id=source_file_id
        )
        
        return scrapped_item

    async def start(self):
        """
        Main processing logic:
        1. Fetch all articles from Eesti API
        2. Compare with database
        3. Process new/changed articles
        """
        self.logger.info("Starting EestiSpider - fetching from API and comparing with database")
        
        # 1. Fetch all articles from API
        menu_data = self.fetch_menu()
        if not menu_data:
            self.logger.error("Failed to fetch menu data")
            return
        
        article_entries = self.extract_article_ids(menu_data)
        self.logger.info(f"Found {len(article_entries)} articles in Eesti.ee API")
        
        if not article_entries:
            self.logger.error("No articles found in API")
            return
        
        # 2. Get existing articles from database
        existing_articles = self.get_existing_articles_from_db()
        self.logger.info(f"Found {len(existing_articles)} existing articles in database")
        
        # 3. Process each article
        processed_count = 0
        skipped_count = 0
        
        for i, article_entry in enumerate(article_entries, 1):
            self.check_source_is_stopping()
            
            external_id = article_entry['id']
            self.logger.info(f"Processing article {i}/{len(article_entries)}: ID {external_id}")
            
            # Fetch current content from API
            article_data = self.fetch_article_from_api(external_id)
            if not article_data:
                self.logger.warning(f"Failed to fetch article {external_id} from API")
                continue
            
            # Check if we should process this article
            should_process = self.should_process_article(article_entry, article_data, existing_articles)
            
            if not should_process:
                skipped_count += 1
                continue
            
            # Add to database if new, or get existing ID
            source_file_id = self.add_or_update_article_in_db(article_entry, existing_articles)
            if not source_file_id:
                self.logger.error(f"Failed to get source_file_id for article {external_id}")
                continue
            
            # Create and yield the scraped item
            scraped_item = self.create_scrapped_item(article_data, article_entry, source_file_id)
            yield scraped_item
            
            processed_count += 1
            
            # Small delay between requests
            if i < len(article_entries):
                time.sleep(self.custom_settings['DOWNLOAD_DELAY'])
        
        self.logger.info(f"EestiSpider completed: {processed_count} processed, {skipped_count} skipped")

    async def parse(self, response: Response, **kwargs):
        """Override parse - not used since we process via API"""
        pass