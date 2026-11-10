import httpx
import pytest

from job_hunt.common.http_client import HttpClient
from job_hunt.common.settings import settings
from job_hunt.job_sources.job_boards.hacker_news_hiring import HackerNewsHiringSource, read_header
from job_hunt.job_sources.job_source import JobSourceError
from job_hunt.job_sources.location_matching import TargetLocationMatcher

# trimmed from the November 2026 thread, as the items endpoint returns it
LONDON_ADVERT = {
    "id": 50340001,
    "created_at": "2026-11-02T16:05:11Z",
    "text": (
        "Juro | AI Engineer (Agentic team) | London, UK | REMOTE (UK) | Full-time | "
        '<a href="https:&#x2F;&#x2F;jobs.ashbyhq.com&#x2F;juro&#x2F;abc?utm_source=hn" rel="nofollow">'
        "https:&#x2F;&#x2F;jobs.ashbyhq.com&#x2F;juro&#x2F;abc</a><p>We&#x27;re hiring our first ML engineer."
    ),
}
LOCATION_FURTHER_DOWN = {
    "id": 50340002,
    "created_at": "2026-11-02T17:00:00Z",
    "text": "Tessl | Research Engineer<p>Location: London (hybrid)",
}
BERLIN_ADVERT = {"id": 50340003, "created_at": "2026-11-02T18:00:00Z", "text": "Acme | SRE | Berlin | ONSITE"}
DELETED_ADVERT = {"id": 50340004, "created_at": "2026-11-02T19:00:00Z", "text": None}

THREAD_SEARCH = {
    "hits": [
        {"objectID": "50338728", "title": "Ask HN: Who is hiring? (November 2026)"},
        {"objectID": "50338726", "title": "Ask HN: Who wants to be hired? (November 2026)"},
        {"objectID": "49922569", "title": "Ask HN: Who is hiring? (October 2026)"},
    ]
}


class FakeHackerNews:
    def __init__(self, search_response=THREAD_SEARCH):
        self.thread_requests: list[str] = []
        self._search_response = search_response

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/search_by_date":
            return httpx.Response(200, json=self._search_response)
        thread_id = request.url.path.rsplit("/", 1)[-1]
        self.thread_requests.append(thread_id)
        children = [LONDON_ADVERT, LOCATION_FURTHER_DOWN, BERLIN_ADVERT, DELETED_ADVERT]
        return httpx.Response(
            200, json={"id": int(thread_id), "title": "Who is hiring?", "children": children}
        )


def make_source(fake_api: FakeHackerNews, cache, threads_to_read: int = 1) -> HackerNewsHiringSource:
    source_settings = settings.job_sources.hacker_news_hiring.model_copy(
        update={"threads_to_read": threads_to_read}
    )
    return HackerNewsHiringSource(
        source_settings,
        TargetLocationMatcher(settings.job_search.target_location_keywords),
        HttpClient(settings.http, httpx.MockTransport(fake_api)),
        cache,
    )


def test_only_adverts_mentioning_a_target_location_are_kept(cache):
    jobs = make_source(FakeHackerNews(), cache).fetch_jobs()
    assert [job.company_name for job in jobs] == ["Juro", "Tessl"]


def test_advert_header_becomes_company_title_and_location(cache):
    juro = make_source(FakeHackerNews(), cache).fetch_jobs()[0]

    assert juro.title == "AI Engineer (Agentic team) | London, UK | REMOTE (UK) | Full-time"
    assert juro.location == "London, UK"
    assert juro.is_remote is True
    assert juro.url == "https://news.ycombinator.com/item?id=50340001"
    # the board link is kept without tracking, so the fetch run can start reading Juro's board
    assert juro.employer_url == "https://jobs.ashbyhq.com/juro/abc"
    assert "first ML engineer" in juro.description


def test_only_the_newest_hiring_threads_are_read(cache):
    fake_api = FakeHackerNews()
    make_source(fake_api, cache, threads_to_read=2).fetch_jobs()
    # "Who wants to be hired?" is skipped even though it's by the same account
    assert fake_api.thread_requests == ["50338728", "49922569"]


def test_missing_thread_is_an_error(cache):
    with pytest.raises(JobSourceError, match="no 'Ask HN: Who is hiring\\?' thread"):
        make_source(FakeHackerNews(search_response={"hits": []}), cache).fetch_jobs()


@pytest.mark.parametrize(
    "advert_text, header",
    [
        ("Juro | AI Engineer | https://juro.com\nmore", ["Juro", "AI Engineer"]),
        ("Taxnova | London | Hybrid", ["Taxnova", "London", "Hybrid"]),
        ("Just a sentence about us", ["Just a sentence about us"]),
    ],
)
def test_header_is_split_on_pipes_without_bare_links(advert_text, header):
    assert read_header(advert_text) == header
