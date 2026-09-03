from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class BaseObject(BaseModel):
    agency_id: str
    source_id: str
    ignore_stopping: bool = False
    is_initial_scrape: bool = False
    quality_control: str | None = None
    extract_images: bool = False


class LinkToScrape(BaseModel):
    url: HttpUrl
    id: str
    hash: str


class SpecifiedLinksScrapeTask(BaseObject):
    urls: list[LinkToScrape]


class SitemapCollectScrapperTask(BaseObject):
    url: HttpUrl


class EntireSourceScrapperTask(BaseObject):
    pass


class EestiScrapperTask(BaseObject):
    """Task for scraping all articles from ARVA/Eesti.ee"""

    pass


class DownloadUrlItem(BaseModel):
    path: str
    download_url: HttpUrl | None = None


class UploadFile(LinkToScrape):
    url: HttpUrl | None = None  # pyright: ignore[reportIncompatibleVariableOverride]
    path: str


class UploadedFileTask(BaseObject):
    download_files: list[DownloadUrlItem]
    urls: list[UploadFile]


class EditedMetadataTask(BaseModel):
    download_url: str
    source_file_id: str
    source_file_path: str


class ApiFileToScrape(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    hash: str
    external_id: str = Field(alias="externalId")


class SpecifiedApiFilesScrapeTask(BaseObject):
    api_files: list[ApiFileToScrape]
