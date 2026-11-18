from collections.abc import Iterator
from typing import Any

from job_hunt.common.cache import Cache, get_cache
from job_hunt.common.http_client import HttpClient, HttpRequestError, get_http_client
from job_hunt.common.logger import get_logger
from job_hunt.common.settings_schema import SerpApiGoogleJobsSourceSettings, Settings
from job_hunt.job_sources.ats_boards.ats_board_links import choose_employer_link
from job_hunt.job_sources.job_source import (
    JobSource,
    JobSourceError,
    register_job_source,
    require_secret,
)
from job_hunt.job_sources.relative_dates import parse_relative_age
from job_hunt.job_store.models import FetchedJob, utc_now

logger = get_logger(__name__)

SEARCH_URL = "https://serpapi.com/search.json"
# free to call; doesn't use up a search
ACCOUNT_URL = "https://serpapi.com/account.json"

GoogleJobsPosting = dict[str, Any]


@register_job_source
class SerpApiGoogleJobsSource(JobSource[GoogleJobsPosting]):
    """Google Jobs through SerpApi. Google Jobs collects adverts from
    employers' own careers pages as well as job boards, so it reaches
    companies that post nowhere else.

    Every page is a paid search, so the plan's remaining searches are checked
    first and pages are cached for the day.
    """

    name = "serpapi_google_jobs"
    display_name = "Google Jobs"
    listing_rank = 60

    def __init__(
        self, source_settings: SerpApiGoogleJobsSourceSettings, http_client: HttpClient, cache: Cache
    ):
        self._settings = source_settings
        self._http_client = http_client
        self._cache = cache

    @classmethod
    def from_settings(cls, settings: Settings) -> "SerpApiGoogleJobsSource":
        return cls(settings.job_sources.serpapi_google_jobs, get_http_client(), get_cache())

    def _list_raw_postings(self) -> Iterator[GoogleJobsPosting]:
        api_key = require_secret(self.name, self._settings.api_key, "api_key")
        self._check_searches_left(api_key)
        failed_terms = 0
        for search_term in self._settings.search_terms:
            try:
                postings = list(self._search_all_pages(api_key, search_term))
            except HttpRequestError as error:
                failed_terms += 1
                logger.warning("%s: search for %r failed: %s", self.name, search_term, error)
                continue
            logger.info("%s: %d results for %r", self.name, len(postings), search_term)
            yield from postings
        if failed_terms == len(self._settings.search_terms):
            raise JobSourceError(f"{self.name}: every one of {failed_terms} searches failed")

    # Stops before the month's searches run out, so the last ones aren't
    # spent on a run that can't finish.
    def _check_searches_left(self, api_key: str) -> None:
        account = self._http_client.get_json(ACCOUNT_URL, params={"api_key": api_key})
        searches_left = account.get("total_searches_left", account.get("plan_searches_left", 0))
        searches_needed = len(self._settings.search_terms) * self._settings.pages_per_search
        if searches_left - searches_needed < self._settings.searches_kept_in_reserve:
            raise JobSourceError(
                f"{self.name}: {searches_left} searches left this month, this run needs up to "
                f"{searches_needed} and {self._settings.searches_kept_in_reserve} are kept in reserve"
            )

    def _search_all_pages(self, api_key: str, search_term: str) -> Iterator[GoogleJobsPosting]:
        next_page_token: str | None = None
        for _ in range(self._settings.pages_per_search):
            page = self._search_page(api_key, search_term, next_page_token)
            yield from page.get("jobs_results", [])
            next_page_token = (page.get("serpapi_pagination") or {}).get("next_page_token")
            if not next_page_token:
                return

    def _search_page(self, api_key: str, search_term: str, next_page_token: str | None) -> dict[str, Any]:
        params = {
            "engine": "google_jobs",
            "q": search_term,
            "location": self._settings.location,
            "google_domain": self._settings.google_domain,
            "gl": self._settings.country_code,
            "hl": "en",
        }
        if next_page_token:
            params["next_page_token"] = next_page_token
        # the key isn't part of the cache key: the results don't depend on it
        cache_key = f"{self.name}:" + "&".join(f"{name}={value}" for name, value in sorted(params.items()))
        return self._cache.get_or_compute(
            cache_key,
            lambda: self._http_client.get_json(SEARCH_URL, params=params | {"api_key": api_key}),
            ttl_hours=self._settings.cache_ttl_hours,
        )

    def _to_fetched_job(self, raw_posting: GoogleJobsPosting) -> FetchedJob:
        extensions = raw_posting.get("detected_extensions") or {}
        apply_links = [
            option["link"] for option in raw_posting.get("apply_options", []) if option.get("link")
        ]
        return FetchedJob(
            source=self.name,
            source_job_id=raw_posting["job_id"],
            title=raw_posting["title"],
            company_name=raw_posting["company_name"],
            location=raw_posting.get("location"),
            url=raw_posting.get("share_link") or apply_links[0],
            employer_url=choose_employer_link(apply_links),
            description=raw_posting.get("description"),
            posted_at=parse_relative_age(extensions.get("posted_at"), now=utc_now()),
            employment_type=extensions.get("schedule_type"),
            salary_text=extensions.get("salary"),
            is_remote=extensions.get("work_from_home"),
        )
