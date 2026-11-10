import pytest
from ddgs.exceptions import DDGSException, RatelimitException

from job_hunt.common.settings import settings
from job_hunt.company_discovery.board_search import (
    BoardSearchDiscovery,
    BoardSearchError,
    build_board_search_queries,
    company_name_from_slug,
)
from job_hunt.company_discovery.web_search.ddgs_provider import DdgsSearchProvider
from job_hunt.company_discovery.web_search.search_provider import SearchProviderError, SearchResult
from job_hunt.company_discovery.web_search.search_provider_factory import SearchProviderFactory
from job_hunt.job_store.company_repository import CompanyRepository
from job_hunt.job_store.models import AtsPlatform, DiscoveredCompany

# what a site: search really brings back: boards, a job on a board, an ad and
# a page that isn't a board
RESULTS = [
    SearchResult("https://jobs.ashbyhq.com/Perplexity/d5bc2302", "Data Scientist"),
    SearchResult("https://jobs.ashbyhq.com/perplexity", "Perplexity jobs"),
    SearchResult("https://propertyfinder.teamtailor.com/jobs/8450997-data-scientist", "Data Scientist"),
    SearchResult("https://www.bing.com/aclick?ld=e8YXfR", "Data scientist jobs in London"),
    SearchResult("https://www.bamboohr.com/resources/data-at-work/", "Data at work"),
]


class FakeSearchProvider:
    name = "fake"

    def __init__(self, results=RESULTS, failing_queries=(), fail_every_search=False):
        self.queries: list[str] = []
        self._results = results
        self._failing_queries = set(failing_queries)
        self._fail_every_search = fail_every_search

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        self.queries.append(query)
        if self._fail_every_search or query in self._failing_queries:
            raise SearchProviderError(f"search for {query!r} failed: rate limited")
        return self._results[:max_results]


def make_discovery(
    provider, database, cache, queries=("site:jobs.ashbyhq.com data scientist London",), **setting_overrides
):
    discovery_settings = settings.company_discovery.model_copy(update=setting_overrides)
    return BoardSearchDiscovery(
        provider, list(queries), discovery_settings, CompanyRepository(database), cache
    )


def test_every_ats_domain_is_searched_for_every_phrase_and_place():
    queries = build_board_search_queries(settings.company_discovery, settings.job_search)
    assert "site:jobs.ashbyhq.com data scientist London" in queries
    assert "site:recruitee.com machine learning engineer United Kingdom" in queries
    domains_per_query = len(settings.company_discovery.role_phrases) * len(settings.job_search.locations)
    assert len(queries) % domains_per_query == 0


def test_boards_in_results_are_tracked_once_each(database, cache):
    report = make_discovery(FakeSearchProvider(), database, cache).run()

    assert [(company.ats_platform, company.ats_board_slug) for company in report.new_companies] == [
        (AtsPlatform.ASHBY, "perplexity"),
        (AtsPlatform.TEAMTAILOR, "propertyfinder"),
    ]
    tracked = CompanyRepository(database).list_active_companies()
    assert {company.name for company in tracked} == {"Perplexity", "Propertyfinder"}
    assert tracked[0].discovered_via == "web search (fake)"


def test_boards_already_tracked_are_found_but_not_new(database, cache):
    CompanyRepository(database).add_company_if_new(
        DiscoveredCompany(
            name="Perplexity",
            ats_platform=AtsPlatform.ASHBY,
            ats_board_slug="perplexity",
            discovered_via="seed list",
        )
    )
    report = make_discovery(FakeSearchProvider(), database, cache).run()
    assert len(report.boards_found) == 2
    assert [company.ats_board_slug for company in report.new_companies] == ["propertyfinder"]


def test_dry_run_reports_new_boards_without_tracking_them(database, cache):
    report = make_discovery(FakeSearchProvider(), database, cache).run(dry_run=True)
    assert len(report.new_companies) == 2
    assert CompanyRepository(database).list_active_companies() == []


def test_rerun_reads_searches_from_the_cache(database, cache):
    provider = FakeSearchProvider()
    make_discovery(provider, database, cache).run()
    make_discovery(provider, database, cache).run()
    assert len(provider.queries) == 1


def test_one_failed_search_doesnt_stop_the_rest(database, cache):
    provider = FakeSearchProvider(failing_queries=["q1"])
    report = make_discovery(provider, database, cache, queries=["q1", "q2"]).run()
    assert report.failed_queries == 1
    assert len(report.new_companies) == 2


def test_run_fails_when_every_search_fails(database, cache):
    with pytest.raises(BoardSearchError):
        make_discovery(FakeSearchProvider(failing_queries=["q1"]), database, cache, queries=["q1"]).run()


def test_each_run_spends_its_budget_and_the_next_carries_on(database, cache):
    provider = FakeSearchProvider()
    queries = ["q1", "q2", "q3", "q4", "q5"]

    first = make_discovery(provider, database, cache, queries=queries, max_searches_per_run=2).run()
    second = make_discovery(provider, database, cache, queries=queries, max_searches_per_run=2).run()

    assert (first.searches_run, first.deferred_queries) == (2, 3)
    assert (second.cached_queries, second.searches_run, second.deferred_queries) == (2, 2, 1)
    assert provider.queries == ["q1", "q2", "q3", "q4"]


def test_run_stops_once_the_engine_keeps_failing(database, cache):
    provider = FakeSearchProvider(fail_every_search=True)
    queries = [f"q{number}" for number in range(10)]
    with pytest.raises(BoardSearchError, match="rate-limiting"):
        make_discovery(provider, database, cache, queries=queries, max_consecutive_failures=3).run()
    assert provider.queries == ["q0", "q1", "q2"]


def test_failures_in_a_row_are_counted_afresh_after_a_success(database, cache):
    provider = FakeSearchProvider(failing_queries=["q1", "q2", "q4", "q5"])
    queries = ["q1", "q2", "q3", "q4", "q5", "q6"]
    report = make_discovery(provider, database, cache, queries=queries, max_consecutive_failures=3).run()
    assert (report.searches_run, report.failed_queries, report.deferred_queries) == (6, 4, 0)


def test_empty_answers_are_not_cached(database, cache):
    provider = FakeSearchProvider(results=[])
    make_discovery(provider, database, cache).run()
    make_discovery(provider, database, cache).run()
    assert len(provider.queries) == 2


@pytest.mark.parametrize(
    "slug, name", [("property-finder", "Property Finder"), ("miracare.na", "Miracare"), ("cfc", "Cfc")]
)
def test_company_name_is_tidied_from_the_slug(slug, name):
    assert company_name_from_slug(slug) == name


class FakeDdgsClient:
    def __init__(self, response):
        self.calls: list[dict] = []
        self._response = response

    def text(self, query, **options):
        self.calls.append({"query": query, **options})
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def make_ddgs(response) -> tuple[DdgsSearchProvider, FakeDdgsClient]:
    client = FakeDdgsClient(response)
    return DdgsSearchProvider(settings.company_discovery.ddgs, client=client), client


def test_ddgs_results_become_search_results_with_the_configured_region():
    provider, client = make_ddgs(
        [{"href": "https://cfc.pinpointhq.com/postings/1", "title": "Analyst"}, {"title": "no link"}]
    )
    assert provider.search("site:pinpointhq.com data London", 5) == [
        SearchResult("https://cfc.pinpointhq.com/postings/1", "Analyst")
    ]
    assert client.calls[0]["region"] == "uk-en"
    assert client.calls[0]["max_results"] == 5


def test_ddgs_no_results_is_empty_not_an_error():
    provider, _ = make_ddgs(DDGSException("No results found."))
    assert provider.search("site:pinpointhq.com nothing", 5) == []


def test_ddgs_rate_limit_is_a_provider_error():
    provider, _ = make_ddgs(RatelimitException("202 Ratelimit"))
    with pytest.raises(SearchProviderError, match="Ratelimit"):
        provider.search("site:pinpointhq.com data", 5)


def test_factory_builds_the_configured_provider():
    assert isinstance(SearchProviderFactory.create(settings), DdgsSearchProvider)
