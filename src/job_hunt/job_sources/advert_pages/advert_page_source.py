import html
import re
from abc import abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from job_hunt.common.cache import Cache, get_cache
from job_hunt.common.cache.backends import CACHE_MISS
from job_hunt.common.http_client import HttpClient, HttpRequestError, get_http_client
from job_hunt.common.logger import get_logger
from job_hunt.common.settings_schema import AdvertPageSourceSettings, JobSearchSettings, Settings
from job_hunt.job_sources.ats_boards.ats_board_links import find_ats_board, strip_tracking_params
from job_hunt.job_sources.html_to_text import html_to_text
from job_hunt.job_sources.job_posting_markup import (
    JobPostingMarkup,
    find_job_posting_markup,
    is_remote_posting,
    read_employment_type,
    read_location_text,
    read_locations,
    read_organisation_name,
    read_salary_text,
)
from job_hunt.job_sources.job_source import UNKNOWN_COMPANY_NAME, JobSource, JobSourceError
from job_hunt.job_sources.location_matching import TargetLocationMatcher
from job_hunt.job_sources.timestamps import parse_iso_timestamp
from job_hunt.job_store.models import FetchedJob

logger = get_logger(__name__)

LINK_PATTERN = re.compile(r"href=[\"']([^\"']+)[\"']", re.IGNORECASE)


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


class AdvertPageSource(JobSource[AdvertPage]):
    """A site with one page per advert carrying schema.org JobPosting markup.

    Subclasses say which advert pages to read (from a sitemap, a search
    page, ...); reading the markup, caching each page and the location check
    are shared. Only pages the site's robots.txt allows are read.
    """

    def __init__(
        self,
        search_settings: JobSearchSettings,
        source_settings: AdvertPageSourceSettings,
        http_client: HttpClient,
        cache: Cache,
    ):
        self._search_settings = search_settings
        self._source_settings = source_settings
        self._location_matcher = TargetLocationMatcher(search_settings.target_location_keywords)
        self._http_client = http_client
        self._cache = cache

    @classmethod
    def from_settings(cls, settings: Settings) -> "AdvertPageSource":
        return cls(
            settings.job_search, getattr(settings.job_sources, cls.name), get_http_client(), get_cache()
        )

    # Pages already in the cache cost nothing; new ones are downloaded up to
    # max_page_downloads_per_run, and the rest wait for the next run, so a
    # first run against a big site can't hold up the daily fetch for hours.
    def _list_raw_postings(self) -> Iterator[AdvertPage]:
        links = self._list_advert_links()
        if not links:
            raise JobSourceError(f"{self.name}: found no advert pages to read")
        pages_read = downloads = failed_pages = deferred_pages = pages_without_markup = 0
        for link in links:
            cache_key = f"{self.name}:advert:{link.url}:{link.version}"
            page_fields = self._cache.get(cache_key, default=CACHE_MISS)
            if page_fields is CACHE_MISS:
                if downloads >= self._source_settings.max_page_downloads_per_run:
                    deferred_pages += 1
                    continue
                downloads += 1
                try:
                    page_fields = self._download_advert_page(link.url)
                except HttpRequestError as error:
                    failed_pages += 1
                    logger.warning("%s: couldn't read %s: %s", self.name, link.url, error)
                    continue
                self._cache.set(cache_key, page_fields, ttl_hours=self._source_settings.page_cache_ttl_hours)
            pages_read += 1
            if page_fields is None:
                pages_without_markup += 1
                continue
            yield AdvertPage(url=link.url, **page_fields)
        logger.info(
            "%s: %d advert pages: %d read (%d downloaded), %d failed, %d left for the next run, "
            "%d without JobPosting markup",
            self.name,
            len(links),
            pages_read,
            downloads,
            failed_pages,
            deferred_pages,
            pages_without_markup,
        )
        if failed_pages and not pages_read:
            raise JobSourceError(f"{self.name}: every one of {failed_pages} advert pages failed")

    # Only what's needed is cached, not the whole page: adverts are mostly
    # layout, and some sites have thousands of them.
    def _download_advert_page(self, url: str) -> dict[str, Any] | None:
        page_html = self._http_client.get_text(url)
        job_posting = find_job_posting_markup(page_html)
        if job_posting is None:
            return None
        links = (strip_tracking_params(html.unescape(link)) for link in LINK_PATTERN.findall(page_html))
        board_links = tuple(dict.fromkeys(link for link in links if find_ats_board(link) is not None))
        return {"job_posting": job_posting, "board_links": board_links}

    def _is_in_scope(self, raw_posting: AdvertPage) -> bool:
        return self._location_matcher.matches_any(read_locations(raw_posting.job_posting))

    def _to_fetched_job(self, raw_posting: AdvertPage) -> FetchedJob:
        job_posting = raw_posting.job_posting
        return FetchedJob(
            source=self.name,
            source_job_id=self._source_job_id(raw_posting),
            title=html_to_text(job_posting["title"]),
            company_name=read_organisation_name(job_posting) or UNKNOWN_COMPANY_NAME,
            location=read_location_text(job_posting),
            url=raw_posting.url,
            employer_url=raw_posting.board_links[0] if raw_posting.board_links else None,
            description=html_to_text(job_posting.get("description")),
            posted_at=parse_iso_timestamp(job_posting.get("datePosted")),
            closes_at=self._closing_date(job_posting),
            employment_type=read_employment_type(job_posting),
            salary_text=read_salary_text(job_posting),
            is_remote=is_remote_posting(job_posting),
        )

    @abstractmethod
    def _list_advert_links(self) -> list[AdvertPageLink]: ...

    # the page path is stable on most sites; override where the id is in it
    def _source_job_id(self, advert_page: AdvertPage) -> str:
        return urlsplit(advert_page.url).path

    def _closing_date(self, job_posting: JobPostingMarkup) -> datetime | None:
        return parse_iso_timestamp(job_posting.get("validThrough"))
