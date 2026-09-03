import contextlib
import datetime
import functools
import re
import typing
from collections.abc import Callable, Iterator
from urllib.parse import urlparse

import requests

from scrapper.items import ScrappedItem

if typing.TYPE_CHECKING:
    from scrapper.spiders.base_spider import BaseSpider
else:
    BaseSpider = object


# Query/form params commonly used to pass credentials or tokens.
_SENSITIVE_PARAM_NAMES = (
    "token",
    "api_key",
    "apikey",
    "password",
    "passwd",
    "pwd",
    "secret",
    "access_token",
    "auth",
    "session",
    "sessionid",
    "sid",
)

_USERINFO_RE = re.compile(r"://[^\s/@]+:[^\s/@]+@")
_AUTH_HEADER_RE = re.compile(
    r"(authorization[\"']?\s*[:=]\s*[\"']?(basic|bearer)\s+)\S+", re.IGNORECASE
)
_SENSITIVE_PARAM_RE = re.compile(
    r"([?&](?:" + "|".join(_SENSITIVE_PARAM_NAMES) + r")=)[^&\s\"'<>]+",
    re.IGNORECASE,
)


def sanitize_sensitive_text(text: str) -> str:
    """Redact credentials/tokens from a URL or error message before it is
    logged or sent to the backend (e.g. userinfo in a URL, Authorization
    headers appearing in exception text, credential-style query params)."""
    if not text:
        return text

    sanitized = _USERINFO_RE.sub("://[redacted]@", text)
    sanitized = _AUTH_HEADER_RE.sub(r"\1[redacted]", sanitized)
    sanitized = _SENSITIVE_PARAM_RE.sub(r"\1[redacted]", sanitized)
    return sanitized


def send_error(
    ruuter_internal: str,
    url: str,
    error_type: str,
    error_message: str,
    source_base_id: str,
    agency_base_id: str,
    source_run_report_base_id: str,
) -> None:
    scraped_at = datetime.datetime.now(datetime.UTC).isoformat()
    requests.post(
        f"{ruuter_internal}/ckb/reports/logs/add",
        json={
            "url": url,
            "scraped_at": scraped_at,
            "error_type": error_type,
            "error_message": sanitize_sensitive_text(error_message),
            "source_base_id": source_base_id,
            "agency_base_id": agency_base_id,
            "source_run_report_base_id": source_run_report_base_id,
        },
    )


@contextlib.contextmanager
def catch_error(url: str, spider: BaseSpider) -> Iterator[None]:
    try:
        yield
    except Exception as e:
        spider.log_error_to_source_run_page(url, "scrapper", str(e))


def catch_error_process_item(f: Callable) -> Callable:
    @functools.wraps(f)
    def process_item(self: object, item: object, spider: BaseSpider) -> object:
        if not isinstance(spider, BaseSpider):
            return item

        if not isinstance(item, ScrappedItem):
            return item

        r = None
        with catch_error(item.metadata.source_url, spider):
            r = f(self, item, spider)
        return r

    return process_item


def catch_error_spider(f: Callable) -> Callable:
    @functools.wraps(f)
    def decorator(self: object, spider: BaseSpider) -> object:
        if not isinstance(spider, BaseSpider):
            return None

        r = None
        with catch_error("internal", spider):
            r = f(self, spider)
        return r

    return decorator


# Archive URL detection keywords in multiple languages
ARCHIVE_KEYWORDS = [
    # Estonian
    "arhiiv",
    "arhiivi",
    "archive",
    # English
    "archived",
    "archives",
    # Russian transliteration
    "arkhiv",
    "arhiv",
]


def is_archive_url(url: str) -> bool:
    """
    Check if a URL points to an archived/historical page.

    Detects archive pages by checking for archive-related keywords in:
    - Subdomain (e.g., arhiiv.example.ee)
    - Path segments (e.g., example.ee/arhiiv/2020/)

    Args:
        url: The URL to check

    Returns:
        True if the URL appears to be an archive page, False otherwise

    Examples:
        >>> is_archive_url('https://arhiiv.lastekaitseliit.ee/et/2016/06/7203/')
        True
        >>> is_archive_url('https://example.com/arhiiv/old-content')
        True
        >>> is_archive_url('https://example.com/current-page')
        False
    """
    try:
        parsed = urlparse(url.lower())

        # Check subdomain for archive keywords
        hostname_parts = parsed.hostname.split(".") if parsed.hostname else []
        for part in hostname_parts:
            if any(keyword in part for keyword in ARCHIVE_KEYWORDS):
                return True

        # Check path segments for archive keywords
        path_parts = parsed.path.split("/")
        for part in path_parts:
            if any(keyword in part for keyword in ARCHIVE_KEYWORDS):
                return True

        return False

    except Exception:
        # If URL parsing fails, don't filter it out
        return False
