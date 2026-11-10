import re
from typing import Any
from urllib.parse import SplitResult

from job_hunt.common.cache import Cache, get_cache
from job_hunt.common.http_client import HttpClient, get_http_client
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
from job_hunt.job_store.models import AtsPlatform, FetchedJob

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

    def _fetch_board_postings(self, board_slug: str) -> list[dict[str, Any]]:
        links = self._job_page_links(board_slug)
        batch = self._page_reader.read_pages(links, self._source_settings.max_page_downloads_per_board)
        return [{"advert_page": page} for page in batch.pages]

    def _job_page_links(self, board_slug: str) -> list[AdvertPageLink]:
        tenant, site = board_slug.split("/", 1)
        sitemap = read_sitemap(self._http_client.get_text(SITEMAP_URL.format(tenant=tenant, site=site)))
        return [AdvertPageLink(entry.url) for entry in sitemap if "/job/" in entry.url]

    def _posting_locations(self, posting: dict[str, Any]) -> list[str | None]:
        return list(read_locations(posting["advert_page"].job_posting))

    # A sample spread across the sitemap, not every page: big employers list
    # thousands of jobs worldwide, and this only decides whether to track them.
    def board_has_jobs_in_target_locations(self, board_slug: str) -> bool:
        links = self._job_page_links(board_slug)
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
