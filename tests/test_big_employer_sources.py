"""Microsoft, Amazon and Meta careers sites, against payloads trimmed from
their live responses (Nov 2026)."""

import json
from datetime import datetime, timezone

import httpx

from job_hunt.common.http_client import HttpClient
from job_hunt.common.settings import settings
from job_hunt.job_sources.advert_pages.meta_careers import MetaCareersSource
from job_hunt.job_sources.job_boards.amazon_jobs import AmazonJobsSource, parse_amazon_date
from job_hunt.job_sources.job_boards.microsoft_careers import MicrosoftCareersSource

MICROSOFT_POSITION = {
    "id": 1970393556928855,
    "name": "Sr Data Scientist - FDE",
    "locations": ["United Kingdom, London, London"],
    "standardizedLocations": ["London, England, GB"],
    "postedTs": 1783694993,
    "department": "Data Science",
    "workLocationOption": "onsite",
    "positionUrl": "/careers/job/1970393556928855",
}
AMAZON_JOB = {
    "id_icims": "10590206",
    "title": "Data Scientist, Edge AI Product Science",
    "company_name": "Evi Technologies Limited",
    "location": "GB, Cambridge",
    "normalized_location": "Cambridge, England, GBR",
    "posted_date": "October 27, 2026",
    "job_path": "/en/jobs/10590206/data-scientist-edge-ai-product-science",
    "job_schedule_type": "full-time",
    "description": "Amazon Devices is an inventive research and development company.",
    "basic_qualifications": "- Master's degree in a quantitative field<br/>- Python",
}


def one_search(search_settings_update=None):
    return settings.job_search.model_copy(
        update={"role_terms": ["data scientist"], "locations": ["London"], **(search_settings_update or {})}
    )


def client_for(answer) -> HttpClient:
    return HttpClient(settings.http, httpx.MockTransport(answer))


def test_microsoft_positions_become_jobs(cache):
    requests: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"status": 200, "data": {"positions": [MICROSOFT_POSITION]}})

    source = MicrosoftCareersSource(
        one_search(), settings.job_sources.microsoft_careers, client_for(answer), cache
    )
    [job] = source.fetch_jobs()

    assert (job.source_job_id, job.title, job.company_name) == (
        "1970393556928855",
        "Sr Data Scientist - FDE",
        "Microsoft",
    )
    assert job.location == "United Kingdom, London, London"
    assert job.url == "https://apply.careers.microsoft.com/careers/job/1970393556928855"
    assert job.posted_at == datetime.fromtimestamp(1783694993, tz=timezone.utc)
    # one short page: no second page asked for
    assert len(requests) == 1
    assert (requests[0].url.params["query"], requests[0].url.params["location"]) == (
        "data scientist",
        "London",
    )


def test_amazon_jobs_become_jobs_limited_to_the_country(cache):
    requests: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"hits": 1, "jobs": [AMAZON_JOB]})

    source = AmazonJobsSource(one_search(), settings.job_sources.amazon_jobs, client_for(answer), cache)
    [job] = source.fetch_jobs()

    assert (job.company_name, job.location) == ("Evi Technologies Limited", "Cambridge, England, GBR")
    assert job.url == "https://www.amazon.jobs/en/jobs/10590206/data-scientist-edge-ai-product-science"
    assert job.posted_at == datetime(2026, 10, 27, tzinfo=timezone.utc)
    assert "Master's degree" in job.description
    assert requests[0].url.params["country"] == "GBR"


def test_amazon_dates_that_cant_be_read_are_unknown():
    assert parse_amazon_date("14 days") is None
    assert parse_amazon_date(None) is None


META_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.metacareers.com/profile/job_details/111/</loc><lastmod>2026-11-09</lastmod></url>
  <url><loc>https://www.metacareers.com/profile/job_details/222/</loc><lastmod>2026-11-09</lastmod></url>
  <url><loc>https://www.metacareers.com/jobs</loc></url>
</urlset>"""


def meta_job_page(title: str, city: str, country: str) -> str:
    markup = {
        "@type": "JobPosting",
        "title": title,
        "hiringOrganization": {"name": "Meta"},
        "jobLocation": [{"address": {"addressLocality": city, "addressCountry": country}}],
        "datePosted": "2026-11-01",
    }
    return f'<script type="application/ld+json">{json.dumps(markup)}</script>'


def test_meta_jobs_come_from_the_sitemap_and_keep_the_uk_ones(cache):
    pages = {
        "/jobsearch/sitemap.xml": META_SITEMAP,
        "/profile/job_details/111/": meta_job_page("Research Scientist, Machine Learning", "London", "GB"),
        "/profile/job_details/222/": meta_job_page("Data Scientist", "Bengaluru", "IN"),
    }

    def answer(request: httpx.Request) -> httpx.Response:
        body = pages.get(request.url.path)
        return httpx.Response(404) if body is None else httpx.Response(200, text=body)

    source = MetaCareersSource(
        settings.job_search, settings.job_sources.meta_careers, client_for(answer), cache
    )
    [job] = source.fetch_jobs()
    assert (job.source_job_id, job.title, job.location) == (
        "111",
        "Research Scientist, Machine Learning",
        "London, United Kingdom",
    )
