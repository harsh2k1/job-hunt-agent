from datetime import timedelta
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from job_hunt.common.http_client import HttpClient
from job_hunt.common.settings import settings
from job_hunt.job_sources.job_boards.serpapi_google_jobs import SerpApiGoogleJobsSource
from job_hunt.job_sources.job_source import JobSourceError
from job_hunt.job_store.models import utc_now

# trimmed from SerpApi's documented google_jobs response
POSTING = {
    "title": "Graduate Data Scientist",
    "company_name": "Marshmallow",
    "location": "London, UK",
    "via": "LinkedIn",
    "description": "Join our pricing team.",
    "job_id": "eyJqb2JfdGl0bGUiOiJHcmFkdWF0ZSJ9",
    "share_link": "https://www.google.com/search?ibp=htl;jobs#htidocid=abc",
    "detected_extensions": {"posted_at": "3 days ago", "schedule_type": "Full-time", "salary": "£35K a year"},
    "apply_options": [
        {"title": "LinkedIn", "link": "https://uk.linkedin.com/jobs/view/123"},
        {"title": "Marshmallow careers", "link": "https://jobs.ashbyhq.com/marshmallow/abc"},
    ],
}


class FakeSerpApi:
    def __init__(self, searches_left: int = 200, search_status: int = 200):
        self.search_requests: list[httpx.Request] = []
        self._searches_left = searches_left
        self._search_status = search_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/account.json":
            return httpx.Response(200, json={"total_searches_left": self._searches_left})
        self.search_requests.append(request)
        if self._search_status != 200:
            return httpx.Response(self._search_status)
        body: dict[str, Any] = {"jobs_results": [POSTING]}
        if "next_page_token" not in request.url.params:
            body["serpapi_pagination"] = {"next_page_token": "page-2"}
        return httpx.Response(200, json=body)


def make_source(fake_api: FakeSerpApi, cache, **setting_overrides) -> SerpApiGoogleJobsSource:
    source_settings = settings.job_sources.serpapi_google_jobs.model_copy(
        update={
            "api_key": SecretStr("test-key"),
            "search_terms": ["graduate data scientist"],
            **setting_overrides,
        }
    )
    return SerpApiGoogleJobsSource(
        source_settings, HttpClient(settings.http, httpx.MockTransport(fake_api)), cache
    )


def test_posting_becomes_a_job_linked_to_the_company_board(cache):
    [job] = make_source(FakeSerpApi(), cache).fetch_jobs()

    assert job.source == "serpapi_google_jobs"
    assert job.company_name == "Marshmallow"
    # the ATS link wins over LinkedIn, so the fetch run can start reading the board
    assert job.employer_url == "https://jobs.ashbyhq.com/marshmallow/abc"
    assert job.salary_text == "£35K a year"
    assert abs(job.posted_at - (utc_now() - timedelta(days=3))) < timedelta(minutes=1)


def test_search_is_sent_for_the_configured_place(cache):
    fake_api = FakeSerpApi()
    make_source(fake_api, cache).fetch_jobs()

    [request] = fake_api.search_requests
    assert request.url.params["engine"] == "google_jobs"
    assert request.url.params["location"] == "London, England, United Kingdom"
    assert request.url.params["api_key"] == "test-key"


def test_extra_pages_follow_the_next_page_token(cache):
    fake_api = FakeSerpApi()
    make_source(fake_api, cache, pages_per_search=2).fetch_jobs()
    assert [request.url.params.get("next_page_token") for request in fake_api.search_requests] == [
        None,
        "page-2",
    ]


def test_rerun_reads_the_cache_instead_of_spending_searches(cache):
    fake_api = FakeSerpApi()
    make_source(fake_api, cache).fetch_jobs()
    make_source(fake_api, cache).fetch_jobs()
    assert len(fake_api.search_requests) == 1


def test_run_is_refused_when_it_would_eat_into_the_reserve(cache):
    fake_api = FakeSerpApi(searches_left=5)
    with pytest.raises(JobSourceError, match="5 searches left"):
        make_source(fake_api, cache, searches_kept_in_reserve=10).fetch_jobs()
    assert fake_api.search_requests == []


def test_missing_api_key_is_a_clear_error(cache):
    with pytest.raises(JobSourceError, match="no api_key"):
        make_source(FakeSerpApi(), cache, api_key=None).fetch_jobs()


def test_source_fails_when_every_search_fails(cache):
    with pytest.raises(JobSourceError, match="every one"):
        make_source(FakeSerpApi(search_status=401), cache).fetch_jobs()
