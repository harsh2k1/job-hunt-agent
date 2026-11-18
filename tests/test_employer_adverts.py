import json
from datetime import datetime, timezone

import pytest

from job_hunt.common.cache.backends import InMemoryCacheBackend
from job_hunt.common.cache.cache import Cache
from job_hunt.common.http_client import HttpRequestError
from job_hunt.common.settings import settings
from job_hunt.company_discovery.web_search.search_provider import (
    SearchProvider,
    SearchProviderError,
    SearchResult,
)
from job_hunt.job_sources.employer_adverts.employer_advert_source import (
    EmployerAdvertSource,
    names_employer,
    title_overlap,
)
from job_hunt.job_sources.employer_adverts.employer_page import EmployerPage, read_employer_page
from job_hunt.job_sources.robots_policy import RobotsPolicy
from job_hunt.job_store.job_repository import JobRepository
from job_hunt.job_store.models import FetchedJob

SAVED = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
ADVERT = "We have an exciting opportunity for an engineer within our AI & Algorithms department. " * 8


@pytest.mark.parametrize(
    "card, page, at_least",
    [
        # a page title ends with the employer's name, and the year moves about
        ("Graduate AI Engineer (2027 start)",
         "Graduate AI Engineer (2027 start) | Cambridge Consultants", 1.0),
        ("Graduate Research Engineer - Digital Engineering",
         "Graduate Research Engineer - Digital Engineering", 1.0),
        ("Data & Analytics Research Fellowship",
         "Oliver Wyman - Data & Analytics Research Fellowship - Newcastle", 1.0),
    ],
)  # fmt: skip
def test_the_same_role_titled_a_little_differently_matches(card, page, at_least):
    assert title_overlap(card, page) >= at_least


def test_a_different_role_at_the_employer_does_not():
    assert title_overlap("Graduate AI Engineer", "Graduate Software Engineer") < 0.7
    assert title_overlap("Graduate Data Scientist", "Careers at Arm") < 0.7


def page(
    url="https://careers.example.com/job/1", title="Graduate AI Engineer", organisation=None, advert=ADVERT
):
    return EmployerPage(url=url, title=title, advert=advert, organisation=organisation)


def test_the_employer_must_be_the_same_one():
    assert names_employer("Arm", page(organisation="Arm Limited"))
    assert names_employer("PA Consulting Group", page(organisation="PA Consulting"))
    # a longer name that only starts the same isn't the same employer
    assert not names_employer("Arm", page(organisation="Armstrong Ltd"))
    # no markup: the employer named in the advert, or in the site's address
    assert names_employer("MTC", page(advert="Here at the MTC, our people are diverse. " + ADVERT))
    assert names_employer("Cambridge Consultants", page(url="https://www.cambridgeconsultants.com/jobs/x"))
    assert not names_employer("Siemens", page())


def test_an_advert_is_read_from_markup_even_when_its_html_was_escaped_twice():
    markup = {
        "@context": "https://schema.org", "@type": "JobPosting",
        "title": "Data &amp; Analytics Research Fellowship",
        "hiringOrganization": {"@type": "Organization", "name": "Oliver Wyman Group"},
        "description": "&lt;div&gt;&lt;p&gt;&lt;b&gt;Who We Are&lt;/b&gt;&lt;/p&gt;"
                       "&lt;p&gt;A consultancy.&lt;/p&gt;&lt;/div&gt;",
    }  # fmt: skip
    html = f'<html><script type="application/ld+json">{json.dumps(markup)}</script></html>'
    read = read_employer_page("https://careers.marsh.com/job/1", html)
    assert (read.title, read.organisation) == ("Data & Analytics Research Fellowship", "Oliver Wyman Group")
    assert "<" not in read.advert and "Who We Are" in read.advert and "A consultancy." in read.advert


# shaped like Cambridge Consultants' careers page: a menu, the advert in
# paragraphs, a footer
def test_a_page_without_markup_gives_its_main_content_not_its_menus():
    menu = "".join(f"<li><a href='/{item}'>{item}</a></li>" for item in ["Home", "Deep tech", "Industries"])
    paragraph = (
        "We have an exciting opportunity for an engineer within our AI & Algorithms department, working on "
        "robotics, sensing and engineering biology projects with talented engineers and scientists."
    )
    html = (
        "<html><head><title>Graduate AI Engineer (2027 start) | Cambridge Consultants</title></head><body>"
        f"<header><nav><ul>{menu}</ul></nav></header><div class='job'><h2>The Role</h2>"
        + f"<p>{paragraph}</p>"
        * 5
        + "<p>Successful candidates can join us between May-October 2027. There is no deadline for this "
        "role, as we recruit on a rolling basis.</p></div>"
        "<footer><p>Cookie settings. Privacy policy.</p></footer></body></html>"
    )
    read = read_employer_page("https://www.cambridgeconsultants.com/jobs/1", html)
    assert read.title == "Graduate AI Engineer (2027 start)"
    assert "join us between May-October 2027" in read.advert
    assert "Cookie settings" not in read.advert and "Deep tech" not in read.advert


class FakeSearch(SearchProvider):
    name = "fake"

    def __init__(self, results: dict[str, list[str]], failing: bool = False):
        self.queries: list[str] = []
        self._results = results
        self._failing = failing

    @classmethod
    def from_settings(cls, settings):
        raise NotImplementedError

    def search(self, query, max_results):
        self.queries.append(query)
        if self._failing:
            raise SearchProviderError("rate limited")
        return [SearchResult(url, "") for url in self._results.get(query, [])]


class FakeWeb:
    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.read: list[str] = []

    def get_text(self, url):
        self.read.append(url)
        if url not in self.pages:
            raise HttpRequestError(f"GET {url} returned 404", status_code=404)
        return self.pages[url]


def advert_page(title: str, employer: str) -> str:
    return (
        f"<html><head><title>{title}</title></head><body><div><h1>{title}</h1>"
        f"<p>Join {employer}. {ADVERT}</p></div></body></html>"
    )


def make_source(database, search, web, **settings_changes):
    cache = Cache(InMemoryCacheBackend(max_entries=1000), "test", 24)
    source_settings = settings.job_sources.employer_adverts.model_copy(update=settings_changes)
    return EmployerAdvertSource(
        source_settings, search, web, cache, JobRepository(database),
        RobotsPolicy(web, cache, "job-hunt", 24), wanted=lambda cards: cards, sleep=lambda seconds: None,
    )  # fmt: skip


def save_cards(database, *titles_and_employers):
    JobRepository(database).save_fetched_jobs(
        [
            FetchedJob(
                source="gradcracker",
                source_job_id=str(number),
                title=title,
                company_name=employer,
                location="London",
                url=f"https://www.gradcracker.com/hub/1/x/graduate-job/{number}/y",
                description="Gradcracker listing: Graduate job",
                employment_type="Graduate job",
            )  # fmt: skip
            for number, (title, employer) in enumerate(titles_and_employers, start=1)
        ],
        SAVED,
    )


def test_the_employers_own_advert_is_found_and_joins_the_card(database):
    save_cards(database, ("Graduate AI Engineer (2027 start)", "Cambridge Consultants"))
    ats_page = "https://job-boards.greenhouse.io/cambridgeconsultants/jobs/123"
    search = FakeSearch({"Graduate AI Engineer (2027 start) Cambridge Consultants": [
        "https://www.gradcracker.com/hub/1088/cambridge-consultants/graduate-job/84889/x",  # never read
        "https://careers.example.com/blocked/1",  # robots.txt says no
        "https://other.example.com/jobs/9",  # another employer's job
        ats_page,
    ]})  # fmt: skip
    web = FakeWeb({
        "https://careers.example.com/robots.txt": "User-agent: *\nDisallow: /blocked/",
        "https://careers.example.com/blocked/1": advert_page("Graduate AI Engineer", "Cambridge Consultants"),
        "https://other.example.com/jobs/9": advert_page("Graduate AI Engineer", "Siemens"),
        ats_page: advert_page(
            "Graduate AI Engineer (2027 start) | Cambridge Consultants", "Cambridge Consultants"
        ),
    })  # fmt: skip
    [copy] = make_source(database, search, web).fetch_jobs()

    # Gradcracker isn't read, nor a page robots.txt refuses
    assert not any("gradcracker" in url for url in web.read)
    assert "https://careers.example.com/blocked/1" not in web.read
    # stored under the card's title, employer and place, so it joins the same listing
    assert (copy.source, copy.source_job_id) == ("employer_adverts", "gradcracker:1")
    assert (copy.title, copy.company_name, copy.location) == (
        "Graduate AI Engineer (2027 start)",
        "Cambridge Consultants",
        "London",
    )
    assert copy.url == ats_page and ADVERT.strip()[:40] in copy.description
    # a page on a company job board: that board gets tracked
    assert copy.employer_url == ats_page


class EverySearchFinds(FakeSearch):
    """Results for every query, none of them the job's advert."""

    def search(self, query, max_results):
        self.queries.append(query)
        return [SearchResult("https://unrelated.example.com/news", "")]


def test_a_card_with_no_copy_found_isnt_searched_again_soon_and_searches_are_capped(database):
    save_cards(
        database, ("Graduate Data Scientist", "Arup"), ("Graduate Analyst", "Arup"), ("AI Intern", "Arup")
    )
    search = EverySearchFinds({})
    source = make_source(database, search, FakeWeb({}), max_searches_per_run=2)
    assert source.fetch_jobs() == []
    assert len(search.queries) == 2
    source.fetch_jobs()
    # the third card's turn; the first two wait until search_again_after_days
    assert len(search.queries) == 3


@pytest.mark.parametrize("failing", [True, False])
def test_an_engine_failing_or_answering_nothing_stops_the_run_and_the_cards_wait_for_the_next(
    database, failing
):
    # DuckDuckGo throttles quietly: "no results" for every query, not an error
    save_cards(database, *[(f"Graduate Data Scientist {number}", "Arup") for number in range(6)])
    search = FakeSearch({}, failing=failing)
    source = make_source(database, search, FakeWeb({}), max_consecutive_failures=3)
    source.fetch_jobs()
    assert len(search.queries) == 3
    # nothing was remembered as searched: the same cards are tried again
    source.fetch_jobs()
    assert search.queries[3:] == search.queries[:3]
