import html
import re
from collections.abc import Iterator
from typing import Any

from job_hunt.common.cache import Cache, get_cache
from job_hunt.common.http_client import HttpClient, get_http_client
from job_hunt.common.logger import get_logger
from job_hunt.common.settings_schema import HackerNewsHiringSourceSettings, Settings
from job_hunt.job_sources.ats_boards.ats_board_links import choose_employer_link
from job_hunt.job_sources.html_to_text import html_to_text
from job_hunt.job_sources.job_source import JobSource, JobSourceError, register_job_source
from job_hunt.job_sources.location_matching import TargetLocationMatcher
from job_hunt.job_sources.timestamps import parse_iso_timestamp
from job_hunt.job_store.models import FetchedJob

logger = get_logger(__name__)

# HN's official search API (run by Algolia); free and needs no key
THREAD_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
THREAD_URL = "https://hn.algolia.com/api/v1/items/{item_id}"
POSTING_PAGE_URL = "https://news.ycombinator.com/item?id={item_id}"
# the account that opens the thread on the first weekday of every month
THREAD_AUTHOR = "whoishiring"
THREAD_TITLE_PREFIX = "Ask HN: Who is hiring?"
LINK_PATTERN = re.compile(r'href="([^"]+)"')

# a top-level comment on the thread, as the items endpoint returns it
HackerNewsComment = dict[str, Any]


@register_job_source
class HackerNewsHiringSource(JobSource[HackerNewsComment]):
    """The monthly "Ask HN: Who is hiring?" thread: every top-level comment is
    one company's advert, mostly startups that post nowhere else.

    Adverts follow a loose convention, a first line of
    "Company | Role | Place | ..." and links further down, so company and
    title come from that line and everything else is kept as the description.
    """

    name = "hacker_news_hiring"
    listing_rank = 80

    def __init__(
        self,
        source_settings: HackerNewsHiringSourceSettings,
        location_matcher: TargetLocationMatcher,
        http_client: HttpClient,
        cache: Cache,
    ):
        self._settings = source_settings
        self._location_matcher = location_matcher
        self._http_client = http_client
        self._cache = cache

    @classmethod
    def from_settings(cls, settings: Settings) -> "HackerNewsHiringSource":
        return cls(
            settings.job_sources.hacker_news_hiring,
            TargetLocationMatcher(settings.job_search.target_location_keywords),
            get_http_client(),
            get_cache(),
        )

    def _list_raw_postings(self) -> Iterator[HackerNewsComment]:
        thread_ids = self._find_latest_thread_ids()
        if not thread_ids:
            raise JobSourceError(f"{self.name}: no {THREAD_TITLE_PREFIX!r} thread found")
        for thread_id in thread_ids:
            thread = self._cache.get_or_compute(
                f"{self.name}:thread:{thread_id}",
                lambda thread_id=thread_id: self._http_client.get_json(THREAD_URL.format(item_id=thread_id)),
                ttl_hours=self._settings.cache_ttl_hours,
            )
            postings = thread.get("children", [])
            logger.info("%s: %d adverts in %r", self.name, len(postings), thread.get("title"))
            yield from postings

    def _find_latest_thread_ids(self) -> list[int]:
        # the same account also opens "Who wants to be hired?" and "Freelancer?"
        response = self._http_client.get_json(
            THREAD_SEARCH_URL, params={"tags": f"story,author_{THREAD_AUTHOR}", "hitsPerPage": 20}
        )
        hiring_threads = [
            int(hit["objectID"])
            for hit in response.get("hits", [])
            if hit.get("title", "").startswith(THREAD_TITLE_PREFIX)
        ]
        return hiring_threads[: self._settings.threads_to_read]

    # Matched on the whole advert, not just the first line: plenty write
    # "Location: London" further down. Deleted comments have no text.
    def _is_in_scope(self, raw_posting: HackerNewsComment) -> bool:
        text = html_to_text(raw_posting.get("text"))
        return text is not None and self._location_matcher.matches(text)

    def _to_fetched_job(self, raw_posting: HackerNewsComment) -> FetchedJob:
        raw_html = raw_posting["text"]
        description = html_to_text(raw_html)
        header = read_header(description)
        company_name, *details = header
        links = [html.unescape(link) for link in LINK_PATTERN.findall(raw_html)]
        return FetchedJob(
            source=self.name,
            source_job_id=str(raw_posting["id"]),
            title=" | ".join(details) or company_name,
            company_name=company_name,
            location=next((part for part in details if self._location_matcher.matches(part)), None),
            url=POSTING_PAGE_URL.format(item_id=raw_posting["id"]),
            employer_url=choose_employer_link(links),
            description=description,
            posted_at=parse_iso_timestamp(raw_posting.get("created_at")),
            is_remote=True if any("remote" in part.lower() for part in details) else None,
        )


# "Juro | AI Engineer | London, UK | https://juro.com" -> ["Juro", "AI Engineer", "London, UK"]
# Bare links are dropped from the header: they're kept in the description and
# picked up as employer links.
def read_header(advert_text: str) -> list[str]:
    first_line = advert_text.strip().split("\n", 1)[0]
    parts = [part.strip() for part in first_line.split("|")]
    return [part for part in parts if part and not part.startswith(("http://", "https://"))] or [first_line]
