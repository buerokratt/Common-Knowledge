import asyncio
from scrapy import Request, Spider
from scrapy.http import Response
from scrapy.core.downloader.handlers.http import HTTPDownloadHandler
from scrapy_playwright.handler import ScrapyPlaywrightDownloadHandler
from playwright._impl._errors import Error as PlaywrightError
from twisted.internet import defer


class DownloadHandler(ScrapyPlaywrightDownloadHandler):
    def __init__(self, crawler):
        super().__init__(crawler)
        self.crawler = crawler
        # Initialize standard HTTP handler for non-Playwright requests
        self._http_handler = HTTPDownloadHandler(
            settings=crawler.settings,
            crawler=crawler
        )

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    def download_request(self, request: Request, spider: Spider):
        """
        Main entry point for downloading requests.
        Check if Playwright is needed, otherwise use direct HTTP.
        """
        # Check if Playwright is requested in meta
        if not request.meta.get('playwright'):
            # Use direct HTTP download for uploaded files
            spider.logger.info(f'Direct HTTP download (no Playwright): {request.url}')
            return self._http_handler.download_request(request, spider)

        # Use Playwright for regular web pages
        spider.logger.info(f'Playwright download: {request.url}')
        return super().download_request(request, spider)

    async def _download_request(self, request: Request, spider: Spider) -> Response:
        """
        Internal async download method with fallback for download errors.
        This is called by the parent's download_request when using Playwright.
        """
        try:
            spider.logger.info(f'Playwright request started: {request.url}')
            async with asyncio.timeout(30):
                r = await super()._download_request(request, spider)
                spider.logger.info(f'Playwright request finished: {request.url}')
                return r
        except TimeoutError:
            spider.logger.warning(f'request timed out due to playwright: {request.url}. Try again')
            await self._close()
            super().__init__(self.crawler)  # Re-initialize with the same crawler
            await self._launch()
            return await self._download_request(request, spider)
        except Exception as e:
            # Catch "Download is starting" and similar download errors as safety net
            if "Download is starting" in str(e) or "net::ERR_ABORTED" in str(e):
                spider.logger.info(f'Download error detected for {request.url}, falling back to direct HTTP download')
                # Fall back to direct HTTP download
                try:
                    import requests
                    # Use requests for simplicity in async context
                    response = requests.get(
                        request.url,
                        headers=dict(request.headers.to_unicode_dict()),
                        timeout=30
                    )
                    response.raise_for_status()

                    spider.logger.info(f'Direct HTTP download completed: {request.url} ({len(response.content)} bytes)')

                    from scrapy.http import HtmlResponse
                    return HtmlResponse(
                        url=response.url,
                        status=response.status_code,
                        headers=dict(response.headers),
                        body=response.content,
                        encoding='utf-8',
                        request=request,
                    )
                except Exception as download_error:
                    spider.logger.error(f'Direct HTTP download failed for {request.url}: {download_error}')
                    raise
            else:
                # Other Playwright errors - re-raise
                spider.logger.error(f'Playwright error for {request.url}: {str(e)}')
                raise
