# This package will contain the spiders of your Scrapy project
#
# Please refer to the documentation for information on how to create and manage
# your spiders.
from scrapy.http import Response
from scrapy.spiders import Spider

class UrlSpider(Spider):
    name = 'url_spider'
    allowed_domains = ['toscrape.com']
    start_urls = ['https://books.toscrape.com/']

    def parse(self, response: Response, **kwargs):
        breakpoint()
