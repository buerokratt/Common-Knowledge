import json
import logging
import sys

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

from api.models import SpecifiedLinksScrapeTask

from scrapper.spiders.uploaded_file_spider import UploadedFileSpider





def main():
    logging.disable(logging.DEBUG)
    task = SpecifiedLinksScrapeTask(**json.loads(sys.argv[1][1:-1]))
    process = CrawlerProcess(get_project_settings())
    process.crawl(UploadedFileSpider, task=task)
    process.start()


if __name__ == '__main__':
    main()
