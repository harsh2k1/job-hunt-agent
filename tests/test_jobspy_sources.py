from datetime import date, datetime, timezone
from typing import Any

import pandas as pd
import pytest

from job_hunt.common.settings import settings
from job_hunt.job_sources.job_boards.jobspy_source import clean_cell, format_salary
from job_hunt.job_sources.job_boards.sites import GoogleJobsSource, IndeedSource, LinkedInSource
from job_hunt.job_sources.job_source import UNKNOWN_COMPANY_NAME, JobSourceError

# one row as JobSpy returned it for a live Indeed search (Nov 2026), trimmed
INDEED_ROW = {
    "id": "in-c97f46cd24e3190a",
    "site": "indeed",
    "job_url": "https://uk.indeed.com/viewjob?jk=c97f46cd24e3190a",
    "job_url_direct": "https://careers.bbc.co.uk/job/Data-Scientist-Apprenticeship",
    "title": "Data Scientist Apprenticeship - Level 6",
    "company": "BBC",
    "location": "London, ENG, UK",
    "date_posted": date(2026, 11, 3),
    "job_type": "fulltime, contract",
    "interval": "yearly",
    "min_amount": 24650.0,
    "max_amount": 24650.0,
    "currency": "GBP",
    "is_remote": False,
    "description": "Job Description\n\nClosing date: 24 November",
    "emails": None,
}


class FakeScrapeJobs:
    """Stands in for jobspy.scrape_jobs: records each call, returns canned rows."""

    def __init__(self, rows: list[dict[str, Any]] | None = None, error: Exception | None = None):
        self.calls: list[dict[str, Any]] = []
        self._rows = rows or []
        self._error = error

    def __call__(self, **arguments: Any) -> pd.DataFrame:
        self.calls.append(arguments)
        if self._error:
            raise self._error
        return pd.DataFrame(self._rows)


def make_source(source_class, fake_scrape: FakeScrapeJobs):
    search_settings = settings.job_search.model_copy(
        update={"role_terms": ["data scientist"], "locations": ["London"]}
    )
    return source_class(search_settings, getattr(settings.job_sources, source_class.name), fake_scrape)


def test_indeed_row_becomes_a_job():
    [job] = make_source(IndeedSource, FakeScrapeJobs([INDEED_ROW])).fetch_jobs()

    assert job.source == "indeed"
    assert job.source_job_id == "in-c97f46cd24e3190a"
    assert job.company_name == "BBC"
    assert job.employer_url == "https://careers.bbc.co.uk/job/Data-Scientist-Apprenticeship"
    assert job.posted_at == datetime(2026, 11, 3, tzinfo=timezone.utc)
    assert job.salary_text == "GBP 24650 yearly"
    assert job.is_remote is False


def test_one_search_per_role_term_per_location():
    fake_scrape = FakeScrapeJobs()
    search_settings = settings.job_search.model_copy(
        update={"role_terms": ["data scientist", "ML engineer"], "locations": ["London", "Cambridge"]}
    )
    IndeedSource(search_settings, settings.job_sources.indeed, fake_scrape).fetch_jobs()

    searched = {(call["search_term"], call["location"]) for call in fake_scrape.calls}
    assert searched == {
        ("data scientist", "London"),
        ("ML engineer", "London"),
        ("data scientist", "Cambridge"),
        ("ML engineer", "Cambridge"),
    }
    assert all(call["site_name"] == ["indeed"] for call in fake_scrape.calls)
    assert all(call["hours_old"] == settings.job_search.max_posting_age_hours for call in fake_scrape.calls)


def test_google_jobs_gets_one_sentence_instead_of_term_and_location():
    fake_scrape = FakeScrapeJobs()
    make_source(GoogleJobsSource, fake_scrape).fetch_jobs()

    [call] = fake_scrape.calls
    assert "search_term" not in call
    assert call["site_name"] == ["google"]
    assert call["google_search_term"] == "data scientist jobs near London since last week"


def test_linkedin_asks_for_descriptions_when_configured():
    fake_scrape = FakeScrapeJobs()
    make_source(LinkedInSource, fake_scrape).fetch_jobs()
    assert (
        fake_scrape.calls[0]["linkedin_fetch_description"] is settings.job_sources.linkedin.fetch_descriptions
    )


def test_missing_company_is_kept_as_unknown():
    row = INDEED_ROW | {"company": float("nan")}
    [job] = make_source(IndeedSource, FakeScrapeJobs([row])).fetch_jobs()
    assert job.company_name == UNKNOWN_COMPANY_NAME


def test_source_fails_when_every_search_fails():
    with pytest.raises(JobSourceError):
        make_source(IndeedSource, FakeScrapeJobs(error=RuntimeError("blocked"))).fetch_jobs()


@pytest.mark.parametrize(
    "value, expected",
    [
        (float("nan"), None),
        (None, None),
        ("  ", None),
        (" BBC ", "BBC"),
        ([], None),
        (["a@b.com"], ["a@b.com"]),
    ],
)
def test_empty_pandas_cells_become_none(value, expected):
    assert clean_cell(value) == expected


@pytest.mark.parametrize(
    "row, expected",
    [
        (
            {"currency": "GBP", "min_amount": 30000.0, "max_amount": 38000.0, "interval": "yearly"},
            "GBP 30000-38000 yearly",
        ),
        ({"currency": "GBP", "min_amount": 15.5, "interval": "hourly"}, "GBP 16 hourly"),
        ({"currency": "GBP"}, None),
    ],
)
def test_salary_is_formatted_from_its_parts(row, expected):
    assert format_salary(row) == expected
