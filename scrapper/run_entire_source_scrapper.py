import json
import sys
import logging

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

from api.models import EntireSourceScrapperTask

from scrapper.spiders.entire_source_spider import EntireSourceSpider





def main():
    logging.disable(logging.DEBUG)
    task = EntireSourceScrapperTask(**json.loads(sys.argv[1][1:-1]))
    process = CrawlerProcess(get_project_settings())
    process.crawl(EntireSourceSpider, task=task)
    process.start()


if __name__ == '__main__':
    main()
