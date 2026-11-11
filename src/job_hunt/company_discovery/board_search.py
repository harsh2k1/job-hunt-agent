import time
from dataclasses import dataclass, field

from job_hunt.common.cache import Cache, get_cache
from job_hunt.common.cache.backends import CACHE_MISS
from job_hunt.common.logger import get_logger
from job_hunt.common.settings import settings as app_settings
from job_hunt.common.settings_schema import CompanyDiscoverySettings, JobSearchSettings, Settings
from job_hunt.company_discovery.web_search.search_provider import SearchProvider, SearchProviderError
from job_hunt.company_discovery.web_search.search_provider_factory import SearchProviderFactory
from job_hunt.job_sources.ats_boards.ats_board_links import (
    AtsBoardLink,
    find_ats_board,
    registered_ats_board_sources,
)
from job_hunt.job_store.company_repository import CompanyRepository
from job_hunt.job_store.models import DiscoveredCompany

logger = get_logger(__name__)


class BoardSearchError(RuntimeError):
    """Not one search could be run, so nothing was learned."""


@dataclass
class BoardSearchReport:
    queries: int = 0
    # answered from the cache, so no search was spent on them
    cached_queries: int = 0
    # live searches sent this run, failed ones included
    searches_run: int = 0
    failed_queries: int = 0
    # left for the next run: the search budget ran out, or the run stopped
    # because the engine kept failing
    deferred_queries: int = 0
    # every board the searches linked to, new or not
    boards_found: list[AtsBoardLink] = field(default_factory=list)
    # boards not tracked before; added unless it was a dry run
    new_companies: list[DiscoveredCompany] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.queries} queries, {self.cached_queries} from cache, {self.searches_run} searched "
            f"({self.failed_queries} failed), {self.deferred_queries} left for the next run; "
            f"{len(self.boards_found)} boards found, {len(self.new_companies)} new"
        )


# One query per ATS domain, role phrase and place, e.g.
# "site:jobs.ashbyhq.com data scientist London". Kept to plain words: quoted
# phrases and long OR chains make the free engines return next to nothing.
def build_board_search_queries(
    discovery_settings: CompanyDiscoverySettings, search_settings: JobSearchSettings
) -> list[str]:
    domains = sorted(
        {domain for source in registered_ats_board_sources() for domain in source.search_domains}
    )
    return [
        f"site:{domain} {role_phrase} {place}"
        for domain in domains
        for place in search_settings.locations
        for role_phrase in discovery_settings.role_phrases
    ]


# Search results only give the board slug; the name is tidied from it
# ("property-finder" -> "Property Finder") and can be corrected by hand.
def company_name_from_slug(board_slug: str) -> str:
    # Teamtailor slugs carry a region: "miracare.na"
    return board_slug.split(".")[0].replace("-", " ").replace("_", " ").title()


class BoardSearchDiscovery:
    """Finds company job boards through web search and starts tracking them.

    Search is only used to find companies, not jobs: once a board is tracked,
    its ATS source reads every job on it directly each day. That keeps the
    number of searches small and lets results be cached for days.
    """

    def __init__(
        self,
        search_provider: SearchProvider,
        queries: list[str],
        discovery_settings: CompanyDiscoverySettings,
        company_repository: CompanyRepository,
        cache: Cache,
    ):
        self._search_provider = search_provider
        self._queries = queries
        self._settings = discovery_settings
        self._company_repository = company_repository
        self._cache = cache

    @classmethod
    def from_settings(cls, settings: Settings = app_settings) -> "BoardSearchDiscovery":
        return cls(
            SearchProviderFactory.create(settings),
            build_board_search_queries(settings.company_discovery, settings.job_search),
            settings.company_discovery,
            CompanyRepository(),
            get_cache(),
        )

    def run(self, dry_run: bool = False) -> BoardSearchReport:
        report = BoardSearchReport(queries=len(self._queries))
        boards = self._collect_boards(report)
        if report.searches_run and report.failed_queries == report.searches_run:
            raise BoardSearchError(
                f"every one of {report.searches_run} searches failed; the search engine may be "
                "rate-limiting, try again later"
            )

        report.boards_found = boards
        for board in report.boards_found:
            company = DiscoveredCompany(
                name=company_name_from_slug(board.ats_board_slug),
                ats_platform=board.ats_platform,
                ats_board_slug=board.ats_board_slug,
                discovered_via=f"web search ({self._search_provider.name})",
            )
            is_new = (
                not self._company_repository.is_known(company.ats_platform, company.ats_board_slug)
                if dry_run
                else self._company_repository.add_company_if_new(company)
            )
            if is_new:
                report.new_companies.append(company)
        logger.info(
            "board search: %d queries, %d from cache, %d searched (%d failed), %d left for later; "
            "%d boards found, %d new",
            report.queries,
            report.cached_queries,
            report.searches_run,
            report.failed_queries,
            report.deferred_queries,
            len(report.boards_found),
            len(report.new_companies),
        )
        return report

    # Free engines block a burst of searches, so each run sends at most
    # max_searches_per_run and picks up where the last one stopped: queries
    # searched within the cache window are answered from the cache, the rest
    # wait. Several failures in a row mean the engine is pushing back, and
    # the run stops rather than keep at it.
    def _collect_boards(self, report: BoardSearchReport) -> list[AtsBoardLink]:
        boards: dict[AtsBoardLink, None] = {}
        consecutive_failures = 0
        for position, query in enumerate(self._queries):
            result_urls = self._cache.get(self._cache_key(query), default=CACHE_MISS)
            if result_urls is not CACHE_MISS:
                report.cached_queries += 1
            elif report.searches_run >= self._settings.max_searches_per_run:
                report.deferred_queries += 1
                continue
            else:
                report.searches_run += 1
                try:
                    result_urls = self._search_live(query)
                except SearchProviderError as error:
                    report.failed_queries += 1
                    consecutive_failures += 1
                    logger.warning("%s", error)
                    if consecutive_failures >= self._settings.max_consecutive_failures:
                        report.deferred_queries += len(self._queries) - position - 1
                        logger.warning(
                            "board search: %d searches failed in a row; stopping", consecutive_failures
                        )
                        break
                    continue
                consecutive_failures = 0
            for url in result_urls:
                board = find_ats_board(url)
                if board is not None:
                    boards.setdefault(board)
        return list(boards)

    # An empty answer isn't cached: from a free engine it's more often a soft
    # block than a true "nothing", so the query is tried again next run.
    def _search_live(self, query: str) -> list[str]:
        result_urls = [
            result.url for result in self._search_provider.search(query, self._settings.results_per_query)
        ]
        if result_urls:
            self._cache.set(self._cache_key(query), result_urls, ttl_hours=self._settings.cache_ttl_hours)
        time.sleep(self._settings.seconds_between_searches)
        return result_urls

    def _cache_key(self, query: str) -> str:
        return f"board_search:{self._search_provider.name}:{self._settings.results_per_query}:{query}"
