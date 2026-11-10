from abc import abstractmethod
from collections.abc import Iterator
from typing import Any

from job_hunt.common.cache import Cache, get_cache
from job_hunt.common.http_client import HttpClient, HttpRequestError, get_http_client
from job_hunt.common.logger import get_logger
from job_hunt.common.settings_schema import JobSearchSettings, SearchApiSourceSettings, Settings
from job_hunt.job_sources.job_boards.job_board_query import JobBoardQuery, build_job_board_queries
from job_hunt.job_sources.job_source import JobSource, JobSourceError

logger = get_logger(__name__)

# one posting as the API returns it
ApiPosting = dict[str, Any]


class SearchApiSource(JobSource[ApiPosting]):
    """A job board with its own keyword search API and numbered pages: one
    search per role term per location, a few pages each.

    Subclasses check their credentials, fetch one page and read one posting;
    paging, caching and the "every search failed" rule are shared.
    """

    def __init__(
        self,
        search_settings: JobSearchSettings,
        source_settings: SearchApiSourceSettings,
        http_client: HttpClient,
        cache: Cache,
    ):
        self._search_settings = search_settings
        self._source_settings = source_settings
        self._http_client = http_client
        self._cache = cache

    @classmethod
    def from_settings(cls, settings: Settings) -> "SearchApiSource":
        return cls(
            settings.job_search, getattr(settings.job_sources, cls.name), get_http_client(), get_cache()
        )

    def _list_raw_postings(self) -> Iterator[ApiPosting]:
        self._check_credentials()
        queries = build_job_board_queries(self._search_settings)
        failed_queries = 0
        for query in queries:
            try:
                postings = list(self._search_all_pages(query))
            except HttpRequestError as error:
                failed_queries += 1
                logger.warning(
                    "%s: search for %r in %s failed: %s", self.name, query.role_term, query.location, error
                )
                continue
            logger.info(
                "%s: %d results for %r in %s", self.name, len(postings), query.role_term, query.location
            )
            yield from postings
        if failed_queries == len(queries):
            raise JobSourceError(f"{self.name}: every one of {len(queries)} searches failed")

    def _search_all_pages(self, query: JobBoardQuery) -> Iterator[ApiPosting]:
        page_size = self._source_settings.results_per_page
        for page_index in range(self._source_settings.pages_per_search):
            postings = self._cache.get_or_compute(
                self._page_cache_key(query, page_index),
                lambda page_index=page_index: self._fetch_page(query, page_index),
                ttl_hours=self._source_settings.cache_ttl_hours,
            )
            yield from postings
            if len(postings) < page_size:
                return

    # everything that changes what a page holds, and nothing secret
    def _page_cache_key(self, query: JobBoardQuery, page_index: int) -> str:
        return ":".join(
            str(part)
            for part in (
                self.name,
                query.location,
                query.role_term,
                self._search_settings.search_radius_miles,
                self._search_settings.max_posting_age_hours,
                self._source_settings.results_per_page,
                page_index,
            )
        )

    @abstractmethod
    def _check_credentials(self) -> None: ...

    # page_index counts from 0; each API numbers its pages its own way
    @abstractmethod
    def _fetch_page(self, query: JobBoardQuery, page_index: int) -> list[ApiPosting]: ...
