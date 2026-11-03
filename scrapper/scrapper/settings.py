"""Scrapy settings for the scrapper project.

For all available options see:
https://docs.scrapy.org/en/latest/topics/settings.html
"""

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_SERVICE_DIR = Path(__file__).resolve().parent.parent

# Service endpoints live in the shared constants.ini, never in code or env.
# docker-compose mounts it at /constants.ini (./scrapper is bind-mounted over
# /app), Helm at /app/constants.ini; locally it is read from the repo root.
CONSTANTS_FILES = (
    _SERVICE_DIR.parent / "constants.ini",
    _SERVICE_DIR / "constants.ini",
)


class _Constants(BaseSettings):
    model_config = SettingsConfigDict(env_file=CONSTANTS_FILES, extra="ignore")

    ckb_ruuter_internal: str = ""
    ckb_eesti_base_url: str = ""


_constants = _Constants()

BOT_NAME = "scrapper"

SPIDER_MODULES = ["scrapper.spiders"]
NEWSPIDER_MODULE = "scrapper.spiders"

ADDONS = {}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36"
)

ROBOTSTXT_OBEY = True

CONCURRENT_REQUESTS = 1
CONCURRENT_REQUESTS_PER_DOMAIN = 1

ITEM_PIPELINES = {
    "scrapper.pipelines.CreateSourceRunReportPipeline": 10,
    "scrapper.pipelines.InitLoggingPipeline": 20,
    "scrapper.pipelines.SetSourceStatusRunningPipeline": 30,
    "scrapper.pipelines.CreateSourceFile": 50,
    "scrapper.pipelines.CreateDirectoryPipeline": 100,
    "scrapper.pipelines.MetadataPipeline": 200,
    "scrapper.pipelines.FilePipeline": 300,
    "scrapper.pipelines.UpdateSourceFile": 400,
    "scrapper.pipelines.TriggerCleaningPipeline": 600,
    "scrapper.pipelines.ScrappingFinishedPipeline": 900,
    "scrapper.pipelines.UploadLogsPipeline": 910,
}

FEED_EXPORT_ENCODING = "utf-8"
ALLOWED_FILETYPES = [
    filetype.strip().lower()
    for filetype in os.environ.get(
        "SUPPORTED_TYPES", ".html,.docx,.doc,.pdf,.md,.txt,.pptx"
    ).split(",")
    if filetype.strip()
]
SCRAPED_DIRECTORY = os.environ.get("SCRAPED_DIRECTORY", "/scrapped-data")
CKB_RUUTER_INTERNAL = _constants.ckb_ruuter_internal
CKB_EESTI_BASE_URL = _constants.ckb_eesti_base_url
DOWNLOAD_DELAY = 0.2

DOWNLOAD_HANDLERS = {
    "http": "scrapper.download_handler.DownloadHandler",
    "https": "scrapper.download_handler.DownloadHandler",
}
PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT = 30_000
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
PLAYWRIGHT_MAX_CONTEXTS = 1
PLAYWRIGHT_MAX_PAGES_PER_CONTEXT = 1
RETRY_TIMES = 10
