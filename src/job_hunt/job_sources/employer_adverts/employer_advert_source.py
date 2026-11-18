"""The employer's own copy of a listing that has no advert text.

Gradcracker cards (saved pages) give a title and a few facts but no JD, and
Gradcracker's advert pages can't be fetched. The same job is nearly always
advertised on the employer's own careers site or job board, so each card
gets one web search for its title and employer. A result is accepted only
when the page's own title matches the card's, the page names the employer
(in its markup, text or address) and it has a real advert; anything less
and the card simply stays without a JD. Only pages the site's robots.txt
allows are read.

An accepted copy is stored under the card's own title, employer and place,
so it joins the card's listing on the sheet and its JD is what's shown and
screened. If the page is on a company job board (Greenhouse, Workday ...),
that board is tracked from then on, so the employer's other jobs are read
every day too. Searches go to cards the sheet would show, newest first, a
few dozen a run; a card with no copy found is searched again after a while.
"""

import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from job_hunt.common.cache import Cache, get_cache
from job_hunt.common.cache.backends import CACHE_MISS
from job_hunt.common.http_client import HttpClient, HttpRequestError, get_http_client
from job_hunt.common.logger import get_logger
from job_hunt.common.settings_schema import EmployerAdvertsSourceSettings, Settings
from job_hunt.company_discovery.web_search.search_provider import SearchProvider, SearchProviderError
from job_hunt.company_discovery.web_search.search_provider_factory import SearchProviderFactory
from job_hunt.job_sources.ats_boards.ats_board_links import board_link_or_none
from job_hunt.job_sources.employer_adverts.employer_page import EmployerPage, read_employer_page
from job_hunt.job_sources.job_source import JobSource, register_job_source
from job_hunt.job_sources.robots_policy import RobotsPolicy
from job_hunt.job_store.company_names import normalise_company_name
from job_hunt.job_store.job_repository import JobRepository
from job_hunt.job_store.models import FetchedJob, StoredJob, utc_now

logger = get_logger(__name__)

# "(2027 start)", "[London]", "| Cambridge Consultants": not part of what the job is
BRACKETED = re.compile(r"[(\[{][^)\]}]*[)\]}]")
TITLE_SUFFIX = re.compile(r"\s[|–—-]\s.*$")
YEAR = re.compile(r"\b20\d\d\b")
WORD = re.compile(r"[a-z0-9]+")
# words that say nothing about which job it is
FILLER_WORDS = frozenset(
    {"the", "and", "of", "in", "for", "a", "an", "to", "at", "with", "programme", "program"}
)
# a page with less is a listing or a teaser, not the advert
MIN_ADVERT_CHARACTERS = 400

# A card -> whether the sheet would show it: not judged unrelated. Given by
# the pipeline (which knows listings and screenings), so this source needn't.
CardIsWanted = Callable[[list[StoredJob]], list[StoredJob]]


@dataclass(frozen=True)
class EmployerCopy:
    card: StoredJob
    page: EmployerPage


def title_words(title: str) -> set[str]:
    text = YEAR.sub(" ", BRACKETED.sub(" ", title.lower())).replace("&", " and ")
    return {word for word in WORD.findall(text) if word not in FILLER_WORDS}


# share of the card's title words the page's title has; a page title's
# " | Employer" or " - Careers" ending is ignored
def title_overlap(card_title: str, page_title: str) -> float:
    card_words = title_words(card_title)
    page_words = title_words(page_title) | title_words(TITLE_SUFFIX.sub("", page_title))
    return len(card_words & page_words) / len(card_words) if card_words else 0.0


# "Arm" and "Arm Limited", "PA Consulting" and "PA Consulting Group": the
# markup's employer starting with the card's (or the other way round), else
# the employer's name in the advert or the site's address
def names_employer(card_company: str, page: EmployerPage) -> bool:
    card = normalise_company_name(card_company)
    if not card:
        return False
    if page.organisation:
        named = normalise_company_name(page.organisation)
        return bool(named) and (f"{named} ".startswith(f"{card} ") or f"{card} ".startswith(f"{named} "))
    host = (urlsplit(page.url).hostname or "").replace("-", "").replace(".", "")
    in_text = f" {card} " in f" {normalise_company_name(page.title + ' ' + page.advert)} "
    return in_text or card.replace(" ", "") in host


def host_is_skipped(url: str, skipped_hosts: list[str]) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == skipped or host.endswith(f".{skipped}") for skipped in skipped_hosts)


@register_job_source
class EmployerAdvertSource(JobSource[EmployerCopy]):
    name = "employer_adverts"
    display_name = "Employer's site"
    # the employer's own advert: the fullest copy after its job board's
    listing_rank = 15
    reads_stored_jobs = True

    def __init__(
        self,
        source_settings: EmployerAdvertsSourceSettings,
        search_provider: SearchProvider,
        http_client: HttpClient,
        cache: Cache,
        job_repository: JobRepository,
        robots: RobotsPolicy,
        wanted: CardIsWanted,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._source_settings = source_settings
        self._search_provider = search_provider
        self._http_client = http_client
        self._cache = cache
        self._job_repository = job_repository
        self._robots = robots
        self._wanted = wanted
        self._sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings) -> "EmployerAdvertSource":
        # imported here: the pipeline imports the job sources
        from job_hunt.job_pipeline.listing_interest import cards_the_sheet_would_show

        source_settings = settings.job_sources.employer_adverts
        http_client, cache = get_http_client(), get_cache()
        return cls(
            source_settings,
            SearchProviderFactory.create(settings),
            http_client,
            cache,
            JobRepository(),
            RobotsPolicy(http_client, cache, settings.http.user_agent, source_settings.page_cache_ttl_hours),
            lambda cards: cards_the_sheet_would_show(settings, cards),
        )

    @classmethod
    def unseen_jobs_listed_since(cls, settings: Settings, now: datetime) -> datetime | None:
        return now - timedelta(days=settings.job_sources.employer_adverts.listed_without_deadline_days)

    def _list_raw_postings(self) -> Iterator[EmployerCopy]:
        cards = self._cards_to_look_up(utc_now())
        searches = failures = found = 0
        for card in cards:
            if searches >= self._source_settings.max_searches_per_run:
                break
            if searches:
                self._sleep(self._source_settings.seconds_between_searches)
            searches += 1
            try:
                results = self._search_provider.search(
                    f"{card.title} {card.company_name}", self._source_settings.results_per_search
                )
            except SearchProviderError as error:
                results, problem = [], str(error)
            else:
                problem = "no results"
            # A real job's title and employer always find something; nothing at
            # all means the engine is throttling quietly. Not a miss: the card
            # is searched again next run, and enough of these stop this one.
            if not results:
                failures += 1
                logger.warning("%s: search for %s gave nothing: %s", self.name, card.title, problem)
                if failures >= self._source_settings.max_consecutive_failures:
                    logger.warning("%s: %d searches gave nothing in a row; stopping", self.name, failures)
                    break
                continue
            failures = 0
            page = self._matching_page(card, [result.url for result in results])
            if page is None:
                search_again_hours = self._source_settings.search_again_after_days * 24
                self._cache.set(self._searched_key(card), True, ttl_hours=search_again_hours)
                continue
            found += 1
            yield EmployerCopy(card, page)
        logger.info(
            "%s: %d listings without an advert; %d searched, %d copies found, %d left for later runs",
            self.name, len(cards), searches, found, max(0, len(cards) - searches),
        )  # fmt: skip

    def _to_fetched_job(self, raw_posting: EmployerCopy) -> FetchedJob:
        card, page = raw_posting.card, raw_posting.page
        # the card's title, employer and place, so the copy joins its listing
        return FetchedJob(
            source=self.name,
            source_job_id=self._copy_id(card),
            title=card.title,
            company_name=card.company_name,
            location=card.location,
            url=page.url,
            # a page on a company job board means that board can be read directly
            employer_url=board_link_or_none(page.url),
            description=page.advert,
            posted_at=page.posted_at,
            closes_at=card.closes_at or page.closes_at,
            employment_type=card.employment_type,
            salary_text=page.salary_text or card.salary_text,
        )

    # Live cards the sheet would show with no copy yet, newest first.
    def _cards_to_look_up(self, now: datetime) -> list[StoredJob]:
        listed_since = now - timedelta(days=self._source_settings.listed_without_deadline_days)
        have_copy = {job.source_job_id for job in self._job_repository.list_jobs_from(self.name)}
        cards = [
            job
            for source in self._source_settings.for_sources
            for job in self._job_repository.list_jobs_from(source)
            if (job.closes_at is None or job.closes_at >= now)
            and (job.closes_at is not None or job.last_seen_at >= listed_since)
            and self._copy_id(job) not in have_copy
            and self._cache.get(self._searched_key(job), default=None) is None
        ]
        # newest first; on a tie, the order the sheet cares about (Jobs and To check first)
        return sorted(self._wanted(cards), key=lambda card: card.first_seen_at, reverse=True)

    def _matching_page(self, card: StoredJob, urls: list[str]) -> EmployerPage | None:
        for url in dict.fromkeys(urls):
            if host_is_skipped(url, self._source_settings.skipped_hosts) or not self._robots.allows(url):
                continue
            page = self._page(url)
            if (
                page is not None
                and len(page.advert) >= MIN_ADVERT_CHARACTERS
                and title_overlap(card.title, page.title) >= self._source_settings.min_title_overlap
                and names_employer(card.company_name, page)
            ):
                return page
        return None

    # each page read once in a while: only what's needed is cached
    def _page(self, url: str) -> EmployerPage | None:
        cache_key = f"{self.name}:page:{url}"
        cached = self._cache.get(cache_key, default=CACHE_MISS)
        if cached is not CACHE_MISS:
            return cached
        try:
            page = read_employer_page(url, self._http_client.get_text(url))
        except HttpRequestError as error:
            logger.info("%s: couldn't read %s: %s", self.name, url, error)
            return None
        self._cache.set(cache_key, page, ttl_hours=self._source_settings.page_cache_ttl_hours)
        return page

    @staticmethod
    def _copy_id(card: StoredJob) -> str:
        return f"{card.source}:{card.source_job_id}"

    def _searched_key(self, card: StoredJob) -> str:
        return f"{self.name}:searched:{self._copy_id(card)}"
