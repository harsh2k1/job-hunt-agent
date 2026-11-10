"""Reading job advert pages that carry schema.org JobPosting markup, shared
by every source that reads them (sitemap sites, Workday careers sites...).
Each page is downloaded once and only its markup and ATS links are cached."""

import html
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from job_hunt.common.cache import Cache
from job_hunt.common.cache.backends import CACHE_MISS
from job_hunt.common.http_client import HttpClient, HttpRequestError
from job_hunt.common.logger import get_logger
from job_hunt.job_sources.html_to_text import html_to_text
from job_hunt.job_sources.job_posting_markup import (
    JobPostingMarkup,
    find_job_posting_markup,
    is_remote_posting,
    read_employment_type,
    read_location_text,
    read_organisation_name,
    read_salary_text,
)
from job_hunt.job_sources.job_source import UNKNOWN_COMPANY_NAME
from job_hunt.job_sources.timestamps import parse_iso_timestamp
from job_hunt.job_store.models import FetchedJob

logger = get_logger(__name__)

LINK_PATTERN = re.compile(r"href=[\"']([^\"']+)[\"']", re.IGNORECASE)
# a link on the page -> the clean link if it's a company ATS board, else None
BoardLinkFinder = Callable[[str], str | None]


@dataclass(frozen=True)
class AdvertPageLink:
    url: str
    # Changes whenever the advert does (a sitemap's lastmod), so a changed
    # page is read again and an unchanged one comes from the cache. None when
    # the site doesn't say; the cached copy then simply expires.
    version: str | None = None


@dataclass(frozen=True)
class AdvertPage:
    url: str
    job_posting: JobPostingMarkup
    # links on the page to a company ATS board, tracking params removed
    board_links: tuple[str, ...]


@dataclass
class AdvertPageBatch:
    pages: list[AdvertPage] = field(default_factory=list)
    # read from the cache or downloaded, with or without markup
    read: int = 0
    downloaded: int = 0
    failed: int = 0
    # over the download cap; read on a later run
    deferred: int = 0
    without_markup: int = 0


class AdvertPageReader:
    def __init__(
        self,
        http_client: HttpClient,
        cache: Cache,
        cache_namespace: str,
        page_cache_ttl_hours: float,
        find_board_link: BoardLinkFinder,
    ):
        self._find_board_link = find_board_link
        self._http_client = http_client
        self._cache = cache
        self._cache_namespace = cache_namespace
        self._page_cache_ttl_hours = page_cache_ttl_hours

    # Pages already in the cache cost nothing; new ones are downloaded up to
    # max_downloads, and the rest wait for a later run, so a first run
    # against a big site can't hold up the daily fetch for hours.
    def read_pages(self, links: Iterable[AdvertPageLink], max_downloads: int) -> AdvertPageBatch:
        batch = AdvertPageBatch()
        for link in links:
            cache_key = f"{self._cache_namespace}:advert:{link.url}:{link.version}"
            page_fields = self._cache.get(cache_key, default=CACHE_MISS)
            if page_fields is CACHE_MISS:
                if batch.downloaded >= max_downloads:
                    batch.deferred += 1
                    continue
                batch.downloaded += 1
                try:
                    page_fields = self._download(link.url)
                except HttpRequestError as error:
                    batch.failed += 1
                    logger.warning("%s: couldn't read %s: %s", self._cache_namespace, link.url, error)
                    continue
                self._cache.set(cache_key, page_fields, ttl_hours=self._page_cache_ttl_hours)
            batch.read += 1
            if page_fields is None:
                batch.without_markup += 1
            else:
                batch.pages.append(AdvertPage(url=link.url, **page_fields))
        return batch

    # Only what's needed is cached, not the whole page: adverts are mostly
    # layout, and some sites have thousands of them.
    def _download(self, url: str) -> dict[str, Any] | None:
        page_html = self._http_client.get_text(url)
        job_posting = find_job_posting_markup(page_html)
        if job_posting is None:
            return None
        board_links = (self._find_board_link(html.unescape(link)) for link in LINK_PATTERN.findall(page_html))
        board_links = tuple(dict.fromkeys(link for link in board_links if link is not None))
        return {"job_posting": job_posting, "board_links": board_links}


# What every JobPosting page gives; a source adds its id and anything the
# markup lacks (a known company name, a closing date read from the text).
def job_from_advert_page(
    source: str,
    source_job_id: str,
    page: AdvertPage,
    company_name: str | None = None,
    closes_at: datetime | None = None,
) -> FetchedJob:
    job_posting = page.job_posting
    return FetchedJob(
        source=source,
        source_job_id=source_job_id,
        title=html_to_text(job_posting["title"]),
        company_name=company_name or read_organisation_name(job_posting) or UNKNOWN_COMPANY_NAME,
        location=read_location_text(job_posting),
        url=page.url,
        employer_url=page.board_links[0] if page.board_links else None,
        description=html_to_text(job_posting.get("description")),
        posted_at=parse_iso_timestamp(job_posting.get("datePosted")),
        closes_at=closes_at or parse_iso_timestamp(job_posting.get("validThrough")),
        employment_type=read_employment_type(job_posting),
        salary_text=read_salary_text(job_posting),
        is_remote=is_remote_posting(job_posting),
    )
