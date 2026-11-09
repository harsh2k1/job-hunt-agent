"""Each ATS reader against payloads shaped like the real API responses
(trimmed copies of what the live APIs returned in Nov 2026)."""

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from job_hunt.common.http_client import HttpClient
from job_hunt.common.settings import settings
from job_hunt.job_sources.ats_boards.ashby import AshbySource
from job_hunt.job_sources.ats_boards.greenhouse import GreenhouseSource
from job_hunt.job_sources.ats_boards.lever import LeverEuSource, LeverSource
from job_hunt.job_sources.ats_boards.smartrecruiters import SmartRecruitersSource
from job_hunt.job_sources.ats_boards.workable import WorkableSource
from job_hunt.job_sources.job_source import JobSourceError
from job_hunt.job_sources.location_matching import TargetLocationMatcher
from job_hunt.job_store.company_repository import CompanyRepository
from job_hunt.job_store.models import AtsPlatform, DiscoveredCompany

# route: (host, path) -> JSON body, or an int for an error status
Routes = dict[tuple[str, str], Any]


def make_http_client(routes: Routes, seen_requests: list[httpx.Request] | None = None) -> HttpClient:
    def answer(request: httpx.Request) -> httpx.Response:
        if seen_requests is not None:
            seen_requests.append(request)
        body = routes[(request.url.host, request.url.path)]
        if isinstance(body, Callable):
            body = body(request)
        if isinstance(body, int):
            return httpx.Response(body)
        return httpx.Response(200, json=body)

    return HttpClient(settings.http, transport=httpx.MockTransport(answer))


def make_source(source_class, database, routes: Routes, seen_requests=None):
    return source_class(
        source_settings=getattr(settings.job_sources, source_class.name),
        location_matcher=TargetLocationMatcher(["london", "uk", "united kingdom"]),
        http_client=make_http_client(routes, seen_requests),
        company_repository=CompanyRepository(database),
    )


def track(database, platform: AtsPlatform, slug: str, name: str) -> None:
    CompanyRepository(database).add_company_if_new(
        DiscoveredCompany(name=name, ats_platform=platform, ats_board_slug=slug, discovered_via="test")
    )


GREENHOUSE_BOARD = {
    "jobs": [
        {
            "id": 5456619008,
            "title": "Research Engineer, Interpretability",
            "company_name": "Anthropic",
            "absolute_url": "https://job-boards.greenhouse.io/anthropic/jobs/5456619008",
            "location": {"name": "London, UK"},
            "offices": [{"name": "London", "location": "London, England, United Kingdom"}],
            "first_published": "2026-10-19T11:17:24-04:00",
            "updated_at": "2026-10-20T09:00:00-04:00",
            "content": "&lt;p&gt;You will study &amp;amp; explain models.&lt;/p&gt;",
        },
        {
            "id": 1,
            "title": "Accountant",
            "company_name": "Anthropic",
            "absolute_url": "https://job-boards.greenhouse.io/anthropic/jobs/1",
            "location": {"name": "San Francisco, CA"},
            "offices": [],
            "updated_at": "2026-10-19T11:17:24-04:00",
            "content": "",
        },
    ],
    "meta": {"total": 2},
}


def test_greenhouse_keeps_target_locations_and_reads_the_advert(database):
    track(database, AtsPlatform.GREENHOUSE, "anthropic", "Anthropic")
    routes = {("boards-api.greenhouse.io", "/v1/boards/anthropic/jobs"): GREENHOUSE_BOARD}
    [job] = make_source(GreenhouseSource, database, routes).fetch_jobs()

    assert job.source == "greenhouse"
    assert job.source_job_id == "anthropic:5456619008"
    assert job.title == "Research Engineer, Interpretability"
    assert job.location == "London, UK; London, England, United Kingdom"
    assert job.description == "You will study & explain models."
    assert job.posted_at == datetime(2026, 10, 19, 15, 17, 24, tzinfo=timezone.utc)


LEVER_BOARD = [
    {
        "id": "6ed76ce8",
        "text": "Forward Deployed Engineer, New Grad",
        "hostedUrl": "https://jobs.lever.co/palantir/6ed76ce8",
        "createdAt": 1786469891368,
        "workplaceType": "onsite",
        "categories": {
            "commitment": "Full-time",
            "location": "London, United Kingdom",
            "allLocations": ["London"],
        },
        "descriptionPlain": "Palantir builds software.",
        "lists": [{"text": "What we value", "content": "<li>Python</li><li>Curiosity</li>"}],
        "additionalPlain": "We sponsor visas where needed.",
    },
    {
        "id": "other",
        "text": "Engineer",
        "hostedUrl": "https://jobs.lever.co/palantir/other",
        "createdAt": 1786469891368,
        "categories": {"location": "Singapore"},
    },
]


@pytest.mark.parametrize(
    "source_class, platform, api_host",
    [
        (LeverSource, AtsPlatform.LEVER, "api.lever.co"),
        (LeverEuSource, AtsPlatform.LEVER_EU, "api.eu.lever.co"),
    ],
)
def test_lever_reads_its_own_region(database, source_class, platform, api_host):
    track(database, platform, "palantir", "Palantir")
    routes = {(api_host, "/v0/postings/palantir"): LEVER_BOARD}
    [job] = make_source(source_class, database, routes).fetch_jobs()

    assert job.source == platform.value
    assert job.employment_type == "Full-time"
    assert job.is_remote is False
    assert job.posted_at == datetime.fromtimestamp(1786469891.368, tz=timezone.utc)
    assert job.description == (
        "Palantir builds software.\n\n"
        "What we value\n- Python\n\n- Curiosity\n\n"
        "We sponsor visas where needed."
    )


ASHBY_BOARD = {
    "jobs": [
        {
            "id": "d5bc2302",
            "title": "AI Engineer",
            "location": "San Francisco",
            "secondaryLocations": [{"location": "London"}],
            "employmentType": "FullTime",
            "isListed": True,
            "isRemote": False,
            "jobUrl": "https://jobs.ashbyhq.com/Perplexity/d5bc2302",
            "publishedAt": "2026-07-30T21:04:56.462+00:00",
            "descriptionPlain": "Build answer engines.",
        },
        {
            "id": "hidden",
            "title": "Unlisted role",
            "location": "London",
            "isListed": False,
            "jobUrl": "https://jobs.ashbyhq.com/Perplexity/hidden",
        },
    ]
}


def test_ashby_uses_secondary_locations_and_skips_unlisted_postings(database):
    track(database, AtsPlatform.ASHBY, "perplexity", "Perplexity")
    routes = {("api.ashbyhq.com", "/posting-api/job-board/perplexity"): ASHBY_BOARD}
    [job] = make_source(AshbySource, database, routes).fetch_jobs()

    assert job.source_job_id == "perplexity:d5bc2302"
    assert job.location == "San Francisco; London"
    assert job.description == "Build answer engines."
    assert job.employment_type == "FullTime"


WORKABLE_BOARD = {
    "name": "Hugging Face",
    "jobs": [
        {
            "title": "Machine Learning Engineer - EMEA Remote",
            "shortcode": "81B46579FE",
            "url": "https://apply.workable.com/j/81B46579FE",
            "published_on": "2026-05-29",
            "employment_type": "Full-time",
            "telecommuting": True,
            "city": "Paris",
            "state": "Île-de-France",
            "country": "France",
            "locations": [
                {"city": "London", "region": "England", "country": "United Kingdom", "hidden": False}
            ],
            "description": "<p>Democratise good ML.</p>",
        }
    ],
}


def test_workable_builds_locations_from_parts(database):
    track(database, AtsPlatform.WORKABLE, "huggingface", "Hugging Face")
    routes = {("apply.workable.com", "/api/v1/widget/accounts/huggingface"): WORKABLE_BOARD}
    [job] = make_source(WorkableSource, database, routes).fetch_jobs()

    assert job.location == "Paris, Île-de-France, France; London, England, United Kingdom"
    assert job.description == "Democratise good ML."
    assert job.is_remote is True
    assert job.posted_at == datetime(2026, 5, 29, tzinfo=timezone.utc)


def make_smartrecruiters_posting(posting_id: str) -> dict[str, Any]:
    return {
        "id": posting_id,
        "name": "Graduate Data Scientist",
        "company": {"identifier": "BoschGroup", "name": "Bosch Group"},
        "releasedDate": "2026-11-09T17:11:06.674Z",
        "location": {"city": "London", "country": "gb", "remote": False},
        "typeOfEmployment": {"label": "Full-time"},
    }


def test_smartrecruiters_pages_through_the_board_filtered_by_country(database):
    track(database, AtsPlatform.SMARTRECRUITERS, "boschgroup", "Bosch")
    page_size = settings.job_sources.smartrecruiters.page_size
    total = page_size + 1

    def answer_page(request: httpx.Request) -> dict[str, Any]:
        offset = int(request.url.params["offset"])
        ids = range(offset, min(offset + page_size, total))
        return {
            "offset": offset,
            "totalFound": total,
            "content": [make_smartrecruiters_posting(str(i)) for i in ids],
        }

    seen_requests: list[httpx.Request] = []
    routes = {("api.smartrecruiters.com", "/v1/companies/boschgroup/postings"): answer_page}
    jobs = make_source(SmartRecruitersSource, database, routes, seen_requests).fetch_jobs()

    assert len(jobs) == total
    assert [request.url.params["offset"] for request in seen_requests] == ["0", str(page_size)]
    assert all(request.url.params["country"] == "gb" for request in seen_requests)
    assert jobs[0].url == "https://jobs.smartrecruiters.com/boschgroup/0"
    assert jobs[0].company_name == "Bosch Group"


def test_missing_board_is_deactivated_and_the_rest_still_read(database):
    track(database, AtsPlatform.ASHBY, "gone", "Gone Ltd")
    track(database, AtsPlatform.ASHBY, "perplexity", "Perplexity")
    routes = {
        ("api.ashbyhq.com", "/posting-api/job-board/gone"): 404,
        ("api.ashbyhq.com", "/posting-api/job-board/perplexity"): ASHBY_BOARD,
    }
    jobs = make_source(AshbySource, database, routes).fetch_jobs()

    assert len(jobs) == 1
    remaining = CompanyRepository(database).list_active_companies(AtsPlatform.ASHBY)
    assert [company.ats_board_slug for company in remaining] == ["perplexity"]


def test_source_fails_only_when_no_board_could_be_read(database):
    track(database, AtsPlatform.ASHBY, "perplexity", "Perplexity")
    routes = {("api.ashbyhq.com", "/posting-api/job-board/perplexity"): 500}
    with pytest.raises(JobSourceError):
        make_source(AshbySource, database, routes).fetch_jobs()


def test_no_tracked_companies_means_no_jobs_and_no_error(database):
    assert make_source(GreenhouseSource, database, routes={}).fetch_jobs() == []


def test_malformed_posting_is_skipped_not_fatal(database):
    track(database, AtsPlatform.ASHBY, "perplexity", "Perplexity")
    broken_board = {"jobs": [{"id": "no-title", "location": "London"}, *ASHBY_BOARD["jobs"]]}
    routes = {("api.ashbyhq.com", "/posting-api/job-board/perplexity"): broken_board}
    assert [job.title for job in make_source(AshbySource, database, routes).fetch_jobs()] == ["AI Engineer"]
