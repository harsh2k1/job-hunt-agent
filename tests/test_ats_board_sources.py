"""Each ATS reader against payloads shaped like the real API responses
(trimmed copies of what the live APIs returned in Nov 2026)."""

import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from job_hunt.common.http_client import HttpClient
from job_hunt.common.settings import settings
from job_hunt.job_sources.ats_boards.ashby import AshbySource
from job_hunt.job_sources.ats_boards.bamboohr import BambooHrSource
from job_hunt.job_sources.ats_boards.greenhouse import GreenhouseSource
from job_hunt.job_sources.ats_boards.lever import LeverEuSource, LeverSource
from job_hunt.job_sources.ats_boards.personio import PersonioSource
from job_hunt.job_sources.ats_boards.pinpoint import PinpointSource
from job_hunt.job_sources.ats_boards.recruitee import RecruiteeSource
from job_hunt.job_sources.ats_boards.smartrecruiters import SmartRecruitersSource
from job_hunt.job_sources.ats_boards.teamtailor import TeamtailorSource
from job_hunt.job_sources.ats_boards.workable import WorkableSource
from job_hunt.job_sources.ats_boards.workday import WorkdaySource
from job_hunt.job_sources.job_source import JobSourceError
from job_hunt.job_sources.location_matching import TargetLocationMatcher
from job_hunt.job_store.company_repository import CompanyRepository
from job_hunt.job_store.models import AtsPlatform, DiscoveredCompany

# route: (host, path) -> JSON body, a str for an XML/RSS body, or an int for an error status
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
        # XML and RSS feeds
        if isinstance(body, str):
            return httpx.Response(200, text=body)
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


RECRUITEE_BOARD = {
    "offers": [
        {
            "id": 2709801,
            "title": "Machine Learning Engineer",
            "careers_url": "https://adarga.recruitee.com/o/machine-learning-engineer",
            "location": "Bristol, England, United Kingdom",
            "locations": [{"city": "London", "country": "United Kingdom"}],
            "description": "<p>Build AI systems.</p>",
            "requirements": "<p>Python.</p>",
            "published_at": "2026-08-13 14:26:26 UTC",
            "close_at": "2026-12-01 00:00:00 UTC",
            "employment_type_code": "fulltime_permanent",
            "salary": {"min": 50000, "max": 65000, "period": "year", "currency": "GBP"},
            "remote": False,
        },
        {
            "id": 1,
            "title": "Platform Engineer - US",
            "careers_url": "https://adarga.recruitee.com/o/platform-engineer-us",
            "location": "Herndon, Virginia, United States",
            "locations": [],
        },
    ]
}


def test_recruitee_reads_both_advert_halves_and_the_deadline(database):
    track(database, AtsPlatform.RECRUITEE, "adarga", "Adarga")
    routes = {("adarga.recruitee.com", "/api/offers/"): RECRUITEE_BOARD}
    [job] = make_source(RecruiteeSource, database, routes).fetch_jobs()

    assert job.location == "Bristol, England, United Kingdom; London, United Kingdom"
    assert job.description == "Build AI systems.\n\nPython."
    assert job.posted_at == datetime(2026, 8, 13, 14, 26, 26, tzinfo=timezone.utc)
    assert job.closes_at == datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert job.salary_text == "GBP 50000-65000 year"


PINPOINT_BOARD = {
    "data": [
        {
            "id": "160931",
            "title": "Graduate Data Analyst",
            "url": "https://cfc.pinpointhq.com/en/postings/b43cc73e",
            "location": {"city": "London", "name": "UK - London"},
            "description": "<div>Join the data team.</div>",
            "key_responsibilities": "<div>Build dashboards.</div>",
            "deadline_at": "2026-11-30T23:59:00Z",
            "employment_type_text": "Permanent - Full Time",
            "workplace_type": "hybrid",
            "compensation_visible": False,
            "compensation_minimum": 30000,
        },
        {
            "id": "2",
            "title": "Underwriter",
            "url": "https://cfc.pinpointhq.com/en/postings/2",
            "location": {"name": "Austin, TX"},
        },
    ]
}


def test_pinpoint_joins_the_advert_sections_and_hides_unpublished_salaries(database):
    track(database, AtsPlatform.PINPOINT, "cfc", "CFC")
    routes = {("cfc.pinpointhq.com", "/postings.json"): PINPOINT_BOARD}
    [job] = make_source(PinpointSource, database, routes).fetch_jobs()

    assert job.title == "Graduate Data Analyst"
    assert job.description == "Join the data team.\n\nBuild dashboards."
    assert job.closes_at == datetime(2026, 11, 30, 23, 59, tzinfo=timezone.utc)
    # the employer chose not to show it
    assert job.salary_text is None


PERSONIO_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<workzag-jobs>
<position>
    <id>2877545</id>
    <office>London</office>
    <additionalOffices><office>Berlin</office></additionalOffices>
    <name>Junior Data Scientist </name>
    <jobDescriptions>
        <jobDescription>
            <name>Your mission</name>
            <value><![CDATA[<p>Model patient outcomes.</p>]]></value>
        </jobDescription>
    </jobDescriptions>
    <employmentType>permanent</employmentType>
    <schedule>full-time</schedule>
    <createdAt>2026-10-27T09:33:56+00:00</createdAt>
</position>
<position>
    <id>1</id>
    <office>Munich</office>
    <name>Werkstudent</name>
</position>
</workzag-jobs>"""


def test_personio_feed_is_read_from_xml(database):
    track(database, AtsPlatform.PERSONIO, "calmlife", "Calmlife")
    routes = {("calmlife.jobs.personio.de", "/xml"): PERSONIO_FEED}
    [job] = make_source(PersonioSource, database, routes).fetch_jobs()

    assert job.title == "Junior Data Scientist"
    assert job.location == "London; Berlin"
    assert job.url == "https://calmlife.jobs.personio.de/job/2877545?language=en"
    assert job.description == "Your mission\n\nModel patient outcomes."
    assert job.employment_type == "permanent, full-time"


BAMBOOHR_BOARD = {
    "result": [
        {
            "id": "28",
            "jobOpeningName": "Data Engineer ",
            "employmentStatusLabel": "Full-Time",
            "location": {"city": "London", "state": "London, City of"},
            "atsLocation": {"country": "United Kingdom", "city": None},
            "isRemote": None,
        },
        {"id": "31", "jobOpeningName": "QA Engineer", "location": {"city": "Luxembourg"}, "atsLocation": {}},
    ]
}


def test_bamboohr_list_becomes_jobs_without_adverts(database):
    track(database, AtsPlatform.BAMBOOHR, "clearspace", "ClearSpace")
    routes = {("clearspace.bamboohr.com", "/careers/list"): BAMBOOHR_BOARD}
    [job] = make_source(BambooHrSource, database, routes).fetch_jobs()

    assert job.title == "Data Engineer"
    assert job.location == "London, London, City of; United Kingdom"
    assert job.url == "https://clearspace.bamboohr.com/careers/28"
    assert job.description is None


TEAMTAILOR_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:tt="https://teamtailor.com/locations">
  <channel>
    <item>
      <title>Data Scientist</title>
      <description>&lt;p&gt;Credit risk models.&lt;/p&gt;</description>
      <pubDate>Mon, 28 Sep 2026 09:22:38 +0100</pubDate>
      <link>https://creditspring.teamtailor.com/jobs/8462850-data-scientist</link>
      <remoteStatus>hybrid</remoteStatus>
      <guid>8b06eb17-e38b-44d6-bec9-278d9119d134</guid>
      <tt:locations>
        <tt:location>
          <tt:name>London Office</tt:name>
          <tt:city>London</tt:city>
          <tt:country>United Kingdom</tt:country>
        </tt:location>
      </tt:locations>
    </item>
    <item>
      <title>Account Executive</title>
      <link>https://creditspring.teamtailor.com/jobs/1</link>
      <guid>1</guid>
      <tt:locations><tt:location><tt:city>Stockholm</tt:city><tt:country>Sweden</tt:country></tt:location></tt:locations>
    </item>
  </channel>
</rss>"""


def test_teamtailor_rss_feed_becomes_jobs(database):
    track(database, AtsPlatform.TEAMTAILOR, "creditspring", "Creditspring")
    routes = {("creditspring.teamtailor.com", "/jobs.rss"): TEAMTAILOR_FEED}
    [job] = make_source(TeamtailorSource, database, routes).fetch_jobs()

    assert job.source_job_id == "creditspring:8b06eb17-e38b-44d6-bec9-278d9119d134"
    assert job.location == "London, United Kingdom"
    assert job.description == "Credit risk models."
    assert job.posted_at == datetime(2026, 9, 28, 8, 22, 38, tzinfo=timezone.utc)


def test_board_check_needs_a_posting_in_a_target_location(database):
    london_board = GREENHOUSE_BOARD
    vague_board = {"jobs": [{"id": 1, "title": "Engineer", "location": {"name": ""}, "offices": []}]}
    routes = {
        ("boards-api.greenhouse.io", "/v1/boards/anthropic/jobs"): london_board,
        ("boards-api.greenhouse.io", "/v1/boards/vague/jobs"): vague_board,
    }
    source = make_source(GreenhouseSource, database, routes)
    assert source.board_has_jobs_in_target_locations("anthropic")
    # a fetch would keep this posting (no location to rule it out), but it's
    # not enough to start reading the board every day
    assert not source.board_has_jobs_in_target_locations("vague")


WORKDAY_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://jlp.wd3.myworkdayjobs.com/JLPjobs_careers/job/London/Data-Scientist_R-1</loc></url>
  <url><loc>https://jlp.wd3.myworkdayjobs.com/JLPjobs_careers/job/Dublin/Analyst_R-2</loc></url>
  <url><loc>https://jlp.wd3.myworkdayjobs.com/JLPjobs_careers</loc></url>
</urlset>"""


def workday_job_page(title: str, job_id: str, locality: str, country: str) -> str:
    markup = {
        "@type": "JobPosting",
        "title": title,
        "identifier": {"@type": "PropertyValue", "name": title, "value": job_id},
        "datePosted": "2026-11-10",
        "validThrough": "2026-11-24",
        "hiringOrganization": {"name": "John Lewis plc"},
        "jobLocation": {"address": {"addressLocality": locality, "addressCountry": country}},
        "description": "<p>Join our data team.</p>",
    }
    return f'<script type="application/ld+json">{json.dumps(markup)}</script>'


WORKDAY_ROUTES = {
    ("jlp.wd3.myworkdayjobs.com", "/jlpjobs_careers/siteMap.xml"): WORKDAY_SITEMAP,
    ("jlp.wd3.myworkdayjobs.com", "/JLPjobs_careers/job/London/Data-Scientist_R-1"): workday_job_page(
        "Data Scientist", "R-1", "London", "United Kingdom"
    ),
    ("jlp.wd3.myworkdayjobs.com", "/JLPjobs_careers/job/Dublin/Analyst_R-2"): workday_job_page(
        "Analyst", "R-2", "Dublin", "Ireland"
    ),
}


def make_workday(database, cache, seen_requests=None) -> WorkdaySource:
    return WorkdaySource(
        settings.job_sources.workday,
        TargetLocationMatcher(["london", "uk", "united kingdom"]),
        make_http_client(WORKDAY_ROUTES, seen_requests),
        CompanyRepository(database),
        cache,
    )


def test_workday_site_is_read_from_its_sitemap_and_job_pages(database, cache):
    track(database, AtsPlatform.WORKDAY, "jlp.wd3/jlpjobs_careers", "John Lewis")
    [job] = make_workday(database, cache).fetch_jobs()

    assert job.source_job_id == "jlp.wd3/jlpjobs_careers:R-1"
    assert (job.title, job.company_name, job.location) == (
        "Data Scientist",
        "John Lewis",
        "London, United Kingdom",
    )
    assert job.closes_at == datetime(2026, 11, 24, tzinfo=timezone.utc)
    assert job.description == "Join our data team."


def test_workday_job_pages_are_read_once(database, cache):
    track(database, AtsPlatform.WORKDAY, "jlp.wd3/jlpjobs_careers", "John Lewis")
    seen: list[httpx.Request] = []
    make_workday(database, cache, seen).fetch_jobs()
    make_workday(database, cache, seen).fetch_jobs()
    job_page_reads = [request for request in seen if "/job/" in request.url.path]
    assert len(job_page_reads) == 1


def test_workday_board_check_reads_a_sample_of_jobs(database, cache):
    assert make_workday(database, cache).board_has_jobs_in_target_locations("jlp.wd3/jlpjobs_careers")


def test_workday_site_naming_places_only_reads_target_place_jobs(database, cache):
    # this site's addresses name countries, so its Dublin job is never downloaded
    track(database, AtsPlatform.WORKDAY, "jlp.wd3/jlpjobs_careers", "John Lewis")
    seen: list[httpx.Request] = []
    make_workday(database, cache, seen).fetch_jobs()
    assert [request.url.path for request in seen if "/job/" in request.url.path] == [
        "/JLPjobs_careers/job/London/Data-Scientist_R-1"
    ]


def test_workday_board_check_needs_no_page_when_an_address_names_a_target_place(database, cache):
    seen: list[httpx.Request] = []
    assert make_workday(database, cache, seen).board_has_jobs_in_target_locations("jlp.wd3/jlpjobs_careers")
    assert not [request for request in seen if "/job/" in request.url.path]


def test_workday_site_naming_only_towns_has_every_job_read(database, cache):
    towns_sitemap = WORKDAY_SITEMAP.replace("/job/London/", "/job/Bracknell/").replace(
        "/job/Dublin/", "/job/Heathfield/"
    )
    routes = {
        ("jlp.wd3.myworkdayjobs.com", "/jlpjobs_careers/siteMap.xml"): towns_sitemap,
        ("jlp.wd3.myworkdayjobs.com", "/JLPjobs_careers/job/Bracknell/Data-Scientist_R-1"): workday_job_page(
            "Data Scientist", "R-1", "Bracknell", "United Kingdom"
        ),
        ("jlp.wd3.myworkdayjobs.com", "/JLPjobs_careers/job/Heathfield/Analyst_R-2"): workday_job_page(
            "Analyst", "R-2", "Heathfield", "United Kingdom"
        ),
    }
    track(database, AtsPlatform.WORKDAY, "jlp.wd3/jlpjobs_careers", "John Lewis")
    source = WorkdaySource(
        settings.job_sources.workday,
        TargetLocationMatcher(["london", "uk", "united kingdom"]),
        make_http_client(routes),
        CompanyRepository(database),
        cache,
    )
    assert sorted(job.title for job in source.fetch_jobs()) == ["Analyst", "Data Scientist"]


def test_workday_site_naming_country_codes_reads_only_its_uk_jobs(database, cache):
    # Viavi's addresses: /job/Stevenage-GBR/... and /job/Santa-Rosa-CA-USA/...; neither town is a
    # target place, but GBR is the UK, so the US job is never downloaded
    codes_sitemap = WORKDAY_SITEMAP.replace("/job/London/", "/job/Stevenage-GBR/").replace(
        "/job/Dublin/", "/job/Santa-Rosa-CA-USA/"
    )
    routes = {
        ("jlp.wd3.myworkdayjobs.com", "/jlpjobs_careers/siteMap.xml"): codes_sitemap,
        (
            "jlp.wd3.myworkdayjobs.com",
            "/JLPjobs_careers/job/Stevenage-GBR/Data-Scientist_R-1",
        ): workday_job_page("Data Scientist", "R-1", "Stevenage", "United Kingdom"),
    }
    track(database, AtsPlatform.WORKDAY, "jlp.wd3/jlpjobs_careers", "Viavi")
    seen: list[httpx.Request] = []
    source = WorkdaySource(
        settings.job_sources.workday,
        TargetLocationMatcher(["london", "uk", "united kingdom"]),
        make_http_client(routes, seen),
        CompanyRepository(database),
        cache,
    )
    [job] = source.fetch_jobs()
    assert job.title == "Data Scientist"
    assert [request.url.path for request in seen if "/job/" in request.url.path] == [
        "/JLPjobs_careers/job/Stevenage-GBR/Data-Scientist_R-1"
    ]


def test_a_board_answering_with_something_that_isnt_its_feed_is_skipped_and_the_others_still_read(database):
    track(database, AtsPlatform.TEAMTAILOR, "creditspring", "Creditspring")
    track(database, AtsPlatform.TEAMTAILOR, "broken", "Broken Ltd")
    routes = {
        ("creditspring.teamtailor.com", "/jobs.rss"): TEAMTAILOR_FEED,
        # an error page where the RSS feed should be
        ("broken.teamtailor.com", "/jobs.rss"): "<!doctype html><p>Something went wrong",
    }
    [job] = make_source(TeamtailorSource, database, routes).fetch_jobs()
    assert job.title == "Data Scientist"
