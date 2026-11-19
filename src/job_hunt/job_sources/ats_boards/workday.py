import re
import time
from collections.abc import Iterator
from datetime import date
from typing import Any
from urllib.parse import SplitResult

from job_hunt.common.cache import Cache, get_cache
from job_hunt.common.http_client import HttpClient, get_http_client
from job_hunt.common.logger import get_logger
from job_hunt.common.settings_schema import Settings, WorkdaySourceSettings
from job_hunt.job_sources.advert_page_reader import (
    AdvertPage,
    AdvertPageLink,
    AdvertPageReader,
    job_from_advert_page,
)
from job_hunt.job_sources.ats_boards.ats_board_links import board_link_or_none
from job_hunt.job_sources.ats_boards.ats_board_source import AtsBoardSource, BoardPosting
from job_hunt.job_sources.job_posting_markup import read_locations
from job_hunt.job_sources.job_source import register_job_source
from job_hunt.job_sources.location_matching import TargetLocationMatcher
from job_hunt.job_sources.sitemaps import read_sitemap
from job_hunt.job_store.company_repository import CompanyRepository
from job_hunt.job_store.models import AtsPlatform, FetchedJob, TrackedCompany

logger = get_logger(__name__)

BOARD_DOMAIN = "myworkdayjobs.com"
# each careers site publishes its sitemap (robots.txt points to it)
SITEMAP_URL = "https://{tenant}.myworkdayjobs.com/{site}/siteMap.xml"
# "/en-US/JLPjobs_careers/...": a language before the site name
LOCALE_SEGMENT = re.compile(r"^[a-z]{2}(-[a-z]{2})?$", re.IGNORECASE)
# paths on a Workday host that aren't careers sites
NON_SITE_SEGMENTS = frozenset({"wday", "refreshfacet", "closedrecruitment"})


@register_job_source
class WorkdaySource(AtsBoardSource):
    """Workday careers sites ({tenant}.wdN.myworkdayjobs.com/{site}), read
    the way search engines read them: the site's sitemap, then the
    JobPosting markup on each job page. A board's slug is "tenant.wdN/site".

    One page per job, so new pages are capped per board per run and each is
    cached; checking a newly found board reads a small sample of its jobs.
    """

    ats_platform = AtsPlatform.WORKDAY
    display_name = "Workday"
    search_domains = (BOARD_DOMAIN,)
    _source_settings: WorkdaySourceSettings

    def __init__(
        self,
        source_settings: WorkdaySourceSettings,
        location_matcher: TargetLocationMatcher,
        http_client: HttpClient,
        company_repository: CompanyRepository,
        cache: Cache,
    ):
        super().__init__(source_settings, location_matcher, http_client, company_repository)
        self._page_reader = AdvertPageReader(
            http_client, cache, self.name, source_settings.page_cache_ttl_hours, board_link_or_none
        )
        # when this run stops downloading job pages (time.monotonic); set per fetch
        self._deadline: float | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> "WorkdaySource":
        return cls(
            settings.job_sources.workday,
            TargetLocationMatcher(settings.job_search.target_location_keywords),
            get_http_client(),
            CompanyRepository(),
            get_cache(),
        )

    @classmethod
    def board_slug_from_url(cls, url: SplitResult) -> str | None:
        host = url.hostname or ""
        if not host.endswith(f".{BOARD_DOMAIN}"):
            return None
        tenant = host[: -len(BOARD_DOMAIN) - 1]
        segments = [segment for segment in url.path.split("/") if segment]
        while segments and LOCALE_SEGMENT.match(segments[0]):
            segments.pop(0)
        if not segments or segments[0].lower() in NON_SITE_SEGMENTS:
            return None
        return f"{tenant}/{segments[0]}"

    # Workday sites rate-limit hard and don't let up: a run spends at most
    # max_minutes_per_run on them (job pages and board lists alike), and a
    # board that refuses a few pages in a row is left for the next run.
    # Cached pages are read regardless; boards not reached come first next
    # run, so each is read within a few days.
    def _list_raw_postings(self) -> Iterator[BoardPosting]:
        self._deadline = time.monotonic() + self._source_settings.max_minutes_per_run * 60
        yield from super()._list_raw_postings()

    def _boards_in_order(self, companies: list[TrackedCompany]) -> list[TrackedCompany]:
        if not companies:
            return companies
        # each day starts a seventh of the way further round the list
        start = (date.today().toordinal() * max(1, len(companies) // 7)) % len(companies)
        return companies[start:] + companies[:start]

    def _out_of_time(self) -> bool:
        return self._deadline is not None and time.monotonic() >= self._deadline

    def _fetch_board_postings(self, board_slug: str) -> list[dict[str, Any]]:
        links = self._links_worth_reading(self._job_page_links(board_slug))
        batch = self._page_reader.read_pages(
            links,
            self._source_settings.max_page_downloads_per_board,
            deadline=self._deadline,
            stop_after_failures=self._source_settings.stop_board_after_failures,
        )
        if batch.deferred:
            logger.info("workday: %s: %d pages left for the next run", board_slug, batch.deferred)
        return [{"advert_page": page} for page in batch.pages]

    def _job_page_links(self, board_slug: str) -> list[AdvertPageLink]:
        tenant, site = board_slug.split("/", 1)
        sitemap = read_sitemap(self._http_client.get_text(SITEMAP_URL.format(tenant=tenant, site=site)))
        return [AdvertPageLink(entry.url) for entry in sitemap if "/job/" in entry.url]

    # Job addresses carry the place: /job/UK---London/... or /job/Bracknell/...
    # A site whose addresses name target places at all labels every job that
    # way, so only those are read (a global employer's US jobs cost nothing).
    # A site that names none uses town names only (UK retailers), so all of
    # its jobs are read, within the cap.
    def _links_worth_reading(self, links: list[AdvertPageLink]) -> list[AdvertPageLink]:
        in_target_places = [link for link in links if self._names_target_place(link)]
        return in_target_places or links

    def _names_target_place(self, link: AdvertPageLink) -> bool:
        place = job_place_segment(link.url)
        return self._location_matcher.matches(place.replace("-", " ")) or bool(
            UK_PLACE_CODES & set(place.upper().split("-"))
        )

    def _posting_locations(self, posting: dict[str, Any]) -> list[str | None]:
        return list(read_locations(posting["advert_page"].job_posting))

    # Whether a newly found board is worth tracking: a target place in any
    # job address settles it with no page read; otherwise a small sample of
    # pages spread across the sitemap is read.
    def board_has_jobs_in_target_locations(self, board_slug: str) -> bool:
        links = self._job_page_links(board_slug)
        if any(self._names_target_place(link) for link in links):
            return True
        sample_size = self._source_settings.board_check_sample_pages
        step = max(1, len(links) // sample_size)
        batch = self._page_reader.read_pages(links[::step][:sample_size], max_downloads=sample_size)
        return any(
            self._location_matcher.matches(location)
            for page in batch.pages
            for location in read_locations(page.job_posting)
        )

    def _to_fetched_job(self, raw_posting: BoardPosting) -> FetchedJob:
        page: AdvertPage = raw_posting.posting["advert_page"]
        identifier = page.job_posting.get("identifier")
        job_id = identifier.get("value") if isinstance(identifier, dict) else None
        return job_from_advert_page(
            self.name,
            self._source_job_id(raw_posting.company, job_id or page.url.rsplit("/", 1)[-1]),
            page,
            company_name=raw_posting.company.name,
        )


# country codes Workday sites put in job addresses: /job/Stevenage-GBR/...
UK_PLACE_CODES = frozenset({"GBR", "GB"})


# ".../Depop_Careers/job/London-UK/Backend-Engineer_JR6055" -> "London-UK"
def job_place_segment(url: str) -> str:
    _, _, after_job = url.partition("/job/")
    return after_job.split("/", 1)[0]
