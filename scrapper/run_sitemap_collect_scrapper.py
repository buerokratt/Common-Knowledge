import json
import sys
import logging

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

from api.models import SitemapCollectScrapperTask

from scrapper.spiders.sitemap_collect_spider import SitemapCollectSpider





def main():
    logging.disable(logging.DEBUG)
    task = SitemapCollectScrapperTask(**json.loads(sys.argv[1][1:-1]))
    process = CrawlerProcess(get_project_settings())
    process.crawl(SitemapCollectSpider, task=task)
    process.start()


if __name__ == '__main__':
    main()
