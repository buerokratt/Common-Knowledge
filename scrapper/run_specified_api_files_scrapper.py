import json
import logging
import sys

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

from api.models import SpecifiedApiFilesScrapeTask
from scrapper.spiders.specified_api_files_spider import SpecifiedApiFilesSpider


def main():
    logging.disable(logging.DEBUG)
    task = SpecifiedApiFilesScrapeTask(**json.loads(sys.argv[1][1:-1]))
    process = CrawlerProcess(get_project_settings())
    process.crawl(SpecifiedApiFilesSpider, task=task)
    process.start()


if __name__ == '__main__':
    main()