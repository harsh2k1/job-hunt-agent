from job_hunt.common.http_client import HttpRequestError
from job_hunt.common.settings import settings
from job_hunt.company_discovery.crawl_discovery import CrawlIndexDiscovery
from job_hunt.job_store.company_repository import CompanyRepository
from job_hunt.job_store.models import AtsPlatform, DiscoveredCompany

ASHBY_URLS = [
    "https://jobs.ashbyhq.com/anthropic",
    "https://jobs.ashbyhq.com/Perplexity/123",
    "https://jobs.ashbyhq.com/wayve",
    "https://jobs.ashbyhq.com/gone",
    "https://jobs.ashbyhq.com/flaky",
    # not a board, and a board on another ATS: neither counts here
    "https://www.ashbyhq.com/pricing",
    "https://boards.greenhouse.io/monzo",
]


class FakeUrlIndex:
    def __init__(self, crawl_id: str, urls: list[str]):
        self.crawl_id = crawl_id
        self.prefixes_read: list[str] = []
        self._urls = urls

    def urls_under(self, surt_prefix: str):
        self.prefixes_read.append(surt_prefix)
        return iter(self._urls if surt_prefix == "com,ashbyhq,jobs)/" else [])


class FakeAshbyChecker:
    search_domains = ("jobs.ashbyhq.com",)

    def __init__(self, boards_with_uk_jobs=("perplexity", "wayve")):
        self.checked: list[str] = []
        self._boards_with_uk_jobs = set(boards_with_uk_jobs)

    def board_has_jobs_in_target_locations(self, board_slug: str) -> bool:
        self.checked.append(board_slug)
        if board_slug == "gone":
            raise HttpRequestError("GET .../gone returned 404", status_code=404)
        if board_slug == "flaky":
            raise HttpRequestError("GET .../flaky returned 503", status_code=503)
        return board_slug in self._boards_with_uk_jobs


def make_discovery(database, cache, checker, url_indexes=None, **setting_overrides):
    crawl_settings = settings.company_discovery.common_crawl.model_copy(update=setting_overrides)
    return CrawlIndexDiscovery(
        url_indexes or [FakeUrlIndex("CC-MAIN-2026-43", ASHBY_URLS)],
        {AtsPlatform.ASHBY: checker},
        crawl_settings,
        CompanyRepository(database),
        cache,
    )


def tracked_slugs(database) -> list[str]:
    return [company.ats_board_slug for company in CompanyRepository(database).list_active_companies()]


def test_only_boards_with_uk_jobs_are_tracked(database, cache):
    report = make_discovery(database, cache, FakeAshbyChecker()).run()

    assert report.boards_in_crawls == 5
    assert sorted(tracked_slugs(database)) == ["perplexity", "wayve"]
    assert [company.discovered_via for company in report.new_companies] == [
        "common crawl (CC-MAIN-2026-43)"
    ] * 2
    # a board that 404s is as good as empty; a server error is a failed check
    assert (report.boards_checked, report.failed_checks) == (5, 1)


def test_tracked_boards_arent_checked_again(database, cache):
    CompanyRepository(database).add_company_if_new(
        DiscoveredCompany(
            name="Anthropic",
            ats_platform=AtsPlatform.ASHBY,
            ats_board_slug="anthropic",
            discovered_via="seed list",
        )
    )
    checker = FakeAshbyChecker()
    report = make_discovery(database, cache, checker).run()
    assert "anthropic" not in checker.checked
    assert report.already_tracked == 1


def test_boards_without_uk_jobs_are_left_alone_until_the_recheck(database, cache):
    make_discovery(database, cache, FakeAshbyChecker()).run()
    checker = FakeAshbyChecker()
    report = make_discovery(database, cache, checker).run()

    # anthropic (no UK jobs) and gone (404) are remembered; flaky is tried again
    assert checker.checked == ["flaky"]
    assert report.recently_checked == 2


def test_each_run_checks_its_budget_and_the_next_carries_on(database, cache):
    first_checker, second_checker = FakeAshbyChecker(), FakeAshbyChecker()
    first = make_discovery(database, cache, first_checker, max_board_checks_per_run=2).run()
    make_discovery(database, cache, second_checker, max_board_checks_per_run=2).run()

    assert first_checker.checked == ["anthropic", "flaky"]
    assert first.deferred_checks == 3
    # flaky failed, so it's due again; anthropic is remembered
    assert second_checker.checked == ["flaky", "gone"]


def test_dry_run_checks_but_tracks_nothing(database, cache):
    report = make_discovery(database, cache, FakeAshbyChecker()).run(dry_run=True)
    assert len(report.new_companies) == 2
    assert tracked_slugs(database) == []


def test_boards_from_several_crawls_are_merged_and_each_crawl_is_read_once(database, cache):
    older = FakeUrlIndex("CC-MAIN-2026-39", ["https://jobs.ashbyhq.com/synthesia"])
    newer = FakeUrlIndex("CC-MAIN-2026-43", ASHBY_URLS)
    make_discovery(database, cache, FakeAshbyChecker(), url_indexes=[newer, older]).run()
    report = make_discovery(database, cache, FakeAshbyChecker(), url_indexes=[newer, older]).run()

    assert report.boards_in_crawls == 6
    # the second run took both board lists from the cache
    assert newer.prefixes_read == ["com,ashbyhq,jobs)/", "com,ashbyhq,jobs,"]
