"""Adzuna and Reed against payloads shaped like their documented responses."""

import base64
from datetime import timedelta

import httpx
import pytest
from pydantic import SecretStr

from job_hunt.common.http_client import HttpClient
from job_hunt.common.settings import settings
from job_hunt.job_sources.job_boards.adzuna import AdzunaSource, format_contract
from job_hunt.job_sources.job_boards.reed import ReedSource, parse_reed_date
from job_hunt.job_sources.job_source import JobSourceError
from job_hunt.job_store.models import utc_now

ADZUNA_POSTING = {
    "id": "5012345678",
    "title": "Graduate <strong>Data Scientist</strong>",
    "company": {"display_name": "Marshmallow"},
    "location": {"display_name": "Shoreditch, East London", "area": ["UK", "London"]},
    "redirect_url": "https://www.adzuna.co.uk/jobs/land/ad/5012345678",
    "description": "Join our pricing team&hellip;",
    "created": "2026-11-05T10:22:31Z",
    "contract_type": "permanent",
    "contract_time": "full_time",
    "salary_min": 35000,
    "salary_max": 42000,
    "salary_is_predicted": "0",
}


def reed_posting(job_id: int, posted: str) -> dict:
    return {
        "jobId": job_id,
        "employerName": "NatWest Group ",  # Reed leaves stray spaces in names
        "jobTitle": "Graduate Data Scientist",
        "locationName": "London",
        "minimumSalary": 38000.0,
        "maximumSalary": None,
        "currency": "GBP",
        "date": posted,
        "jobDescription": "Our 2027 graduate programme &amp; more",
        "jobUrl": f"https://www.reed.co.uk/jobs/graduate-data-scientist/{job_id}",
    }


class FakeApi:
    """Answers every search with the given pages in turn, then empty pages."""

    def __init__(self, pages: list[list[dict]], status: int = 200):
        self.requests: list[httpx.Request] = []
        self._pages = pages
        self._status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._status != 200:
            return httpx.Response(self._status)
        page = self._pages[len(self.requests) - 1] if len(self.requests) <= len(self._pages) else []
        return httpx.Response(200, json={"results": page})


def search_settings(**overrides):
    return settings.job_search.model_copy(
        update={"role_terms": ["data scientist"], "locations": ["London"], **overrides}
    )


def make_adzuna(fake_api: FakeApi, cache, **overrides) -> AdzunaSource:
    source_settings = settings.job_sources.adzuna.model_copy(
        update={"app_id": SecretStr("id"), "app_key": SecretStr("key"), "results_per_page": 1, **overrides}
    )
    http_client = HttpClient(settings.http, httpx.MockTransport(fake_api))
    return AdzunaSource(search_settings(), source_settings, http_client, cache)


def make_reed(fake_api: FakeApi, cache, **overrides) -> ReedSource:
    source_settings = settings.job_sources.reed.model_copy(
        update={"api_key": SecretStr("reed-key"), "results_per_page": 1, **overrides}
    )
    http_client = HttpClient(settings.http, httpx.MockTransport(fake_api))
    return ReedSource(search_settings(), source_settings, http_client, cache)


def test_adzuna_posting_becomes_a_job(cache):
    [job] = make_adzuna(FakeApi([[ADZUNA_POSTING]]), cache, pages_per_search=1).fetch_jobs()

    assert job.source == "adzuna"
    assert job.title == "Graduate Data Scientist"
    assert job.company_name == "Marshmallow"
    assert job.description == "Join our pricing team…"
    assert job.salary_text == "GBP 35000-42000 yearly"
    assert job.employment_type == "permanent, full-time"
    assert job.posted_at.isoformat() == "2026-11-05T10:22:31+00:00"


def test_adzuna_estimated_salary_is_left_out(cache):
    posting = ADZUNA_POSTING | {"salary_is_predicted": "1"}
    [job] = make_adzuna(FakeApi([[posting]]), cache, pages_per_search=1).fetch_jobs()
    assert job.salary_text is None


def test_adzuna_request_carries_the_search_and_page_number(cache):
    fake_api = FakeApi([[ADZUNA_POSTING], [ADZUNA_POSTING | {"id": "2"}]])
    make_adzuna(fake_api, cache, pages_per_search=3).fetch_jobs()

    # two full pages of one, then an empty third ends the search
    assert [request.url.path for request in fake_api.requests] == [
        "/v1/api/jobs/gb/search/1",
        "/v1/api/jobs/gb/search/2",
        "/v1/api/jobs/gb/search/3",
    ]
    first = fake_api.requests[0].url.params
    assert (first["what"], first["where"], first["app_key"]) == ("data scientist", "London", "key")
    assert first["max_days_old"] == "7"


def test_search_stops_at_the_first_short_page(cache):
    fake_api = FakeApi([[ADZUNA_POSTING], []])
    make_adzuna(fake_api, cache, pages_per_search=5).fetch_jobs()
    assert len(fake_api.requests) == 2


def test_rerun_reads_pages_from_the_cache(cache):
    fake_api = FakeApi([[ADZUNA_POSTING]])
    make_adzuna(fake_api, cache, pages_per_search=1).fetch_jobs()
    make_adzuna(fake_api, cache, pages_per_search=1).fetch_jobs()
    assert len(fake_api.requests) == 1


@pytest.mark.parametrize("missing", ["app_id", "app_key"])
def test_adzuna_without_credentials_is_a_clear_error(cache, missing):
    fake_api = FakeApi([])
    with pytest.raises(JobSourceError, match=f"no {missing}"):
        make_adzuna(fake_api, cache, **{missing: None}).fetch_jobs()
    assert fake_api.requests == []


def test_source_fails_when_every_search_fails(cache):
    with pytest.raises(JobSourceError, match="every one"):
        make_reed(FakeApi([], status=401), cache).fetch_jobs()


def test_reed_sends_the_key_as_basic_auth_and_pages_by_offset(cache):
    today = utc_now().strftime("%d/%m/%Y")
    fake_api = FakeApi([[reed_posting(1, today)], [reed_posting(2, today)]])
    jobs = make_reed(fake_api, cache, pages_per_search=2).fetch_jobs()

    assert [job.source_job_id for job in jobs] == ["1", "2"]
    expected_auth = "Basic " + base64.b64encode(b"reed-key:").decode()
    assert fake_api.requests[0].headers["Authorization"] == expected_auth
    assert [request.url.params["resultsToSkip"] for request in fake_api.requests] == ["0", "1"]


def test_reed_posting_becomes_a_job(cache):
    today = utc_now().strftime("%d/%m/%Y")
    [job] = make_reed(FakeApi([[reed_posting(7, today)]]), cache, pages_per_search=1).fetch_jobs()

    assert job.company_name == "NatWest Group"
    assert job.salary_text == "GBP 38000"
    assert job.description == "Our 2027 graduate programme & more"
    assert job.url == "https://www.reed.co.uk/jobs/graduate-data-scientist/7"


def test_reed_drops_adverts_older_than_the_search_window(cache):
    old = (utc_now() - timedelta(days=30)).strftime("%d/%m/%Y")
    today = utc_now().strftime("%d/%m/%Y")
    fake_api = FakeApi([[reed_posting(1, old), reed_posting(2, today)]])
    jobs = make_reed(fake_api, cache, pages_per_search=1, results_per_page=2).fetch_jobs()
    assert [job.source_job_id for job in jobs] == ["2"]


def test_reed_date_that_cant_be_read_is_unknown():
    assert parse_reed_date("2026-11-09") is None
    assert parse_reed_date(None) is None


def test_contract_parts_read_as_words():
    assert format_contract(None, "part_time") == "part-time"
    assert format_contract(None, None) is None
