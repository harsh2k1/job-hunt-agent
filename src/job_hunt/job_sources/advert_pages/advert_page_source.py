from abc import abstractmethod
from collections.abc import Iterator
from datetime import datetime
from urllib.parse import urlsplit

from job_hunt.common.cache import Cache, get_cache
from job_hunt.common.http_client import HttpClient, get_http_client
from job_hunt.common.logger import get_logger
from job_hunt.common.settings_schema import AdvertPageSourceSettings, JobSearchSettings, Settings
from job_hunt.job_sources.advert_page_reader import (
    AdvertPage,
    AdvertPageLink,
    AdvertPageReader,
    job_from_advert_page,
)
from job_hunt.job_sources.ats_boards.ats_board_links import board_link_or_none
from job_hunt.job_sources.job_posting_markup import JobPostingMarkup, read_locations
from job_hunt.job_sources.job_source import JobSource, JobSourceError
from job_hunt.job_sources.location_matching import TargetLocationMatcher
from job_hunt.job_store.models import FetchedJob

logger = get_logger(__name__)


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
        self._page_reader = AdvertPageReader(
            http_client, cache, self.name, source_settings.page_cache_ttl_hours, board_link_or_none
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "AdvertPageSource":
        return cls(
            settings.job_search, getattr(settings.job_sources, cls.name), get_http_client(), get_cache()
        )

    def _list_raw_postings(self) -> Iterator[AdvertPage]:
        links = self._list_advert_links()
        if not links:
            raise JobSourceError(f"{self.name}: found no advert pages to read")
        batch = self._page_reader.read_pages(links, self._source_settings.max_page_downloads_per_run)
        logger.info(
            "%s: %d advert pages: %d read (%d downloaded), %d failed, %d left for the next run, "
            "%d without JobPosting markup",
            self.name,
            len(links),
            batch.read,
            batch.downloaded,
            batch.failed,
            batch.deferred,
            batch.without_markup,
        )
        if batch.failed and not batch.read:
            raise JobSourceError(f"{self.name}: every one of {batch.failed} advert pages failed")
        yield from batch.pages

    def _is_in_scope(self, raw_posting: AdvertPage) -> bool:
        return self._location_matcher.matches_any(read_locations(raw_posting.job_posting))

    def _to_fetched_job(self, raw_posting: AdvertPage) -> FetchedJob:
        return job_from_advert_page(
            self.name,
            self._source_job_id(raw_posting),
            raw_posting,
            closes_at=self._closing_date(raw_posting.job_posting),
        )

    @abstractmethod
    def _list_advert_links(self) -> list[AdvertPageLink]: ...

    # the page path is stable on most sites; override where the id is in it
    def _source_job_id(self, advert_page: AdvertPage) -> str:
        return urlsplit(advert_page.url).path

    # None lets the markup's validThrough stand; override where a site puts
    # the deadline somewhere else
    def _closing_date(self, job_posting: JobPostingMarkup) -> datetime | None:
        return None
