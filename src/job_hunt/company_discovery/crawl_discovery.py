from dataclasses import dataclass, field

from job_hunt.common.cache import Cache, get_cache
from job_hunt.common.cache.backends import CACHE_MISS
from job_hunt.common.http_client import HttpRequestError, get_http_client
from job_hunt.common.logger import get_logger
from job_hunt.common.settings import settings as app_settings
from job_hunt.common.settings_schema import CommonCrawlDiscoverySettings, Settings
from job_hunt.company_discovery.board_search import company_name_from_slug
from job_hunt.company_discovery.common_crawl.url_index import (
    CommonCrawlUrlIndex,
    find_latest_crawl_ids,
    surt_prefixes,
)
from job_hunt.job_sources.ats_boards.ats_board_links import AtsBoardLink, find_ats_board
from job_hunt.job_sources.ats_boards.ats_board_source import AtsBoardSource
from job_hunt.job_sources.job_source_factory import JobSourceFactory
from job_hunt.job_store.company_repository import CompanyRepository
from job_hunt.job_store.models import AtsPlatform, DiscoveredCompany

logger = get_logger(__name__)


@dataclass
class CrawlDiscoveryReport:
    crawl_ids: list[str] = field(default_factory=list)
    boards_in_crawls: int = 0
    already_tracked: int = 0
    # checked within board_recheck_after_hours and had no target-location jobs
    recently_checked: int = 0
    boards_checked: int = 0
    failed_checks: int = 0
    # over this run's check budget; checked on a later run
    deferred_checks: int = 0
    # boards with target-location jobs; added unless it was a dry run
    new_companies: list[DiscoveredCompany] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{', '.join(self.crawl_ids)}: {self.boards_in_crawls} boards, "
            f"{self.already_tracked} tracked already, {self.recently_checked} checked recently, "
            f"{self.boards_checked} checked now "
            f"({self.failed_checks} failed), {self.deferred_checks} left for the next run; "
            f"{len(self.new_companies)} with jobs in target locations"
        )


class CrawlIndexDiscovery:
    """Finds company boards in Common Crawl's URL index: every crawled page on
    an ATS's domains names a board, so the index lists each ATS's boards
    directly, with no search engine involved.

    That gives thousands of boards worldwide, so each one not yet tracked is
    read once and only tracked if it has a job in a target location. Boards
    without one are remembered and checked again after a while; a run checks
    at most max_board_checks_per_run and the next one carries on.
    """

    def __init__(
        self,
        url_indexes: list[CommonCrawlUrlIndex],
        board_checkers: dict[AtsPlatform, AtsBoardSource],
        discovery_settings: CommonCrawlDiscoverySettings,
        company_repository: CompanyRepository,
        cache: Cache,
    ):
        self._url_indexes = url_indexes
        self._board_checkers = board_checkers
        self._settings = discovery_settings
        self._company_repository = company_repository
        self._cache = cache

    @classmethod
    def from_settings(cls, settings: Settings = app_settings) -> "CrawlIndexDiscovery":
        crawl_settings = settings.company_discovery.common_crawl
        http_client = get_http_client()
        cache = get_cache()
        crawl_ids = cache.get_or_compute(
            f"common_crawl:latest:{crawl_settings.crawls_to_read}",
            lambda: find_latest_crawl_ids(http_client, crawl_settings.crawls_to_read),
            ttl_hours=24,
        )
        return cls(
            [CommonCrawlUrlIndex(http_client, crawl_id) for crawl_id in crawl_ids],
            enabled_board_checkers(settings),
            crawl_settings,
            CompanyRepository(),
            cache,
        )

    def run(self, dry_run: bool = False) -> CrawlDiscoveryReport:
        report = CrawlDiscoveryReport(crawl_ids=[index.crawl_id for index in self._url_indexes])
        boards = self._list_boards_in_crawls()
        report.boards_in_crawls = len(boards)
        for board in boards:
            if self._company_repository.is_known(board.ats_platform, board.ats_board_slug):
                report.already_tracked += 1
            elif self._cache.get(self._check_cache_key(board), default=CACHE_MISS) is not CACHE_MISS:
                report.recently_checked += 1
            elif report.boards_checked >= self._settings.max_board_checks_per_run:
                report.deferred_checks += 1
            else:
                report.boards_checked += 1
                self._check_board(board, report, dry_run)
        logger.info(
            "crawl discovery (%s): %d boards, %d tracked already, %d checked recently, %d checked now "
            "(%d failed), %d left for later, %d new",
            ", ".join(report.crawl_ids),
            report.boards_in_crawls,
            report.already_tracked,
            report.recently_checked,
            report.boards_checked,
            report.failed_checks,
            report.deferred_checks,
            len(report.new_companies),
        )
        return report

    def _check_board(self, board: AtsBoardLink, report: CrawlDiscoveryReport, dry_run: bool) -> None:
        checker = self._board_checkers[board.ats_platform]
        try:
            has_target_jobs = checker.board_has_jobs_in_target_locations(board.ats_board_slug)
        except HttpRequestError as error:
            # a 404 is a board that's gone: as good as "no jobs here"
            if error.status_code != 404:
                report.failed_checks += 1
                logger.warning("crawl discovery: couldn't check %s: %s", board.ats_board_slug, error)
                return
            has_target_jobs = False
        if not has_target_jobs:
            self._cache.set(
                self._check_cache_key(board), False, ttl_hours=self._settings.board_recheck_after_hours
            )
            return
        company = DiscoveredCompany(
            name=company_name_from_slug(board.ats_board_slug),
            ats_platform=board.ats_platform,
            ats_board_slug=board.ats_board_slug,
            discovered_via=f"common crawl ({', '.join(report.crawl_ids)})",
        )
        if dry_run or self._company_repository.add_company_if_new(company):
            report.new_companies.append(company)

    # Sorted, so runs work through the same order and the budget carries on
    # where the last run stopped.
    def _list_boards_in_crawls(self) -> list[AtsBoardLink]:
        boards: set[AtsBoardLink] = set()
        for url_index in self._url_indexes:
            for platform, checker in self._board_checkers.items():
                for domain in checker.search_domains:
                    boards.update(
                        AtsBoardLink(ats_platform=platform, ats_board_slug=slug)
                        for slug in self._boards_on_domain(url_index, domain, platform)
                    )
        return sorted(boards, key=lambda board: (board.ats_platform.value, board.ats_board_slug))

    # A crawl's index never changes, so its board list is cached for as long
    # as the crawl is among the latest.
    def _boards_on_domain(
        self, url_index: CommonCrawlUrlIndex, domain: str, platform: AtsPlatform
    ) -> list[str]:
        def read_index() -> list[str]:
            slugs: set[str] = set()
            for prefix in surt_prefixes(domain):
                for url in url_index.urls_under(prefix):
                    board = find_ats_board(url)
                    if board is not None and board.ats_platform == platform:
                        slugs.add(board.ats_board_slug)
            logger.info(
                "crawl discovery: %d %s boards on %s in %s",
                len(slugs),
                platform.value,
                domain,
                url_index.crawl_id,
            )
            return sorted(slugs)

        return self._cache.get_or_compute(
            f"common_crawl:{url_index.crawl_id}:{platform.value}:{domain}",
            read_index,
            ttl_hours=self._settings.board_list_cache_ttl_hours,
        )

    @staticmethod
    def _check_cache_key(board: AtsBoardLink) -> str:
        return f"common_crawl:checked:{board.ats_platform.value}:{board.ats_board_slug}"


# A board is only worth finding if its ATS is read; disabled ATSs are skipped.
def enabled_board_checkers(settings: Settings) -> dict[AtsPlatform, AtsBoardSource]:
    checkers: dict[AtsPlatform, AtsBoardSource] = {}
    for source_name in JobSourceFactory.registered_source_names():
        if not JobSourceFactory.is_enabled(source_name, settings):
            continue
        source = JobSourceFactory.create(source_name, settings)
        if isinstance(source, AtsBoardSource):
            checkers[source.ats_platform] = source
    return checkers
