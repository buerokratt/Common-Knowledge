import json
import sys
import logging

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

from api.models import EestiScrapperTask
from scrapper.spiders.eesti_spider import EestiSpider


def main():
    logging.disable(logging.DEBUG)
    task = EestiScrapperTask(**json.loads(sys.argv[1][1:-1]))
    process = CrawlerProcess(get_project_settings())
    process.crawl(EestiSpider, task=task)
    process.start()


if __name__ == '__main__':
    main()