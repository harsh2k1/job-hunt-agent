from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, ClassVar

import pandas as pd
from jobspy import scrape_jobs

from job_hunt.common.logger import get_logger
from job_hunt.common.settings_schema import JobBoardSourceSettings, JobSearchSettings, Settings
from job_hunt.job_sources.job_source import JobSource, JobSourceError
from job_hunt.job_store.models import FetchedJob

logger = get_logger(__name__)

# a JobSpy result row: column name -> cell value
JobSpyRow = dict[str, Any]
ScrapeJobsFunction = Callable[..., pd.DataFrame]

# Google Jobs sometimes leaves the employer out; the job is kept so it can be
# checked by hand rather than silently lost.
UNKNOWN_COMPANY_NAME = "Unknown"


@dataclass(frozen=True)
class JobBoardQuery:
    role_term: str
    location: str


class JobSpySource(JobSource[JobSpyRow]):
    """A big job board read through JobSpy: one search per role term per
    location. Subclasses name the JobSpy site and add site-only options."""

    jobspy_site_name: ClassVar[str]

    def __init__(
        self,
        search_settings: JobSearchSettings,
        source_settings: JobBoardSourceSettings,
        scrape_jobs_function: ScrapeJobsFunction = scrape_jobs,
    ):
        self._search_settings = search_settings
        self._source_settings = source_settings
        self._scrape_jobs = scrape_jobs_function

    @classmethod
    def from_settings(cls, settings: Settings) -> "JobSpySource":
        return cls(settings.job_search, getattr(settings.job_sources, cls.name))

    def _list_raw_postings(self) -> Iterator[JobSpyRow]:
        queries = [
            JobBoardQuery(role_term, location)
            for location in self._search_settings.locations
            for role_term in self._search_settings.role_terms
        ]
        failed_queries = 0
        for query in queries:
            try:
                results = self._scrape_jobs(**self._build_scrape_arguments(query))
            # JobSpy lets errors from its HTTP and parsing libraries through
            # unwrapped, so there's no narrower type to catch
            except Exception as error:
                failed_queries += 1
                logger.warning(
                    "%s: search for %r in %s failed: %s", self.name, query.role_term, query.location, error
                )
                continue
            logger.info(
                "%s: %d results for %r in %s", self.name, len(results), query.role_term, query.location
            )
            yield from results.to_dict("records")
        if failed_queries == len(queries):
            raise JobSourceError(f"{self.name}: every one of {len(queries)} searches failed")

    def _build_scrape_arguments(self, query: JobBoardQuery) -> dict[str, Any]:
        return {
            "site_name": [self.jobspy_site_name],
            "search_term": query.role_term,
            "location": query.location,
            "distance": self._search_settings.search_radius_miles,
            "results_wanted": self._source_settings.results_per_search,
            "hours_old": self._search_settings.max_posting_age_hours,
            "country_indeed": self._search_settings.indeed_country,
            "description_format": "markdown",
            "verbose": 0,
        }

    def _to_fetched_job(self, raw_posting: JobSpyRow) -> FetchedJob:
        row = {column: clean_cell(value) for column, value in raw_posting.items()}
        return FetchedJob(
            source=self.name,
            # the id is unique per site; fall back to the URL if a site leaves it out
            source_job_id=str(row.get("id") or row["job_url"]),
            title=row["title"],
            company_name=row.get("company") or UNKNOWN_COMPANY_NAME,
            location=row.get("location"),
            url=row["job_url"],
            employer_url=row.get("job_url_direct"),
            description=row.get("description"),
            posted_at=parse_posted_date(row.get("date_posted")),
            employment_type=row.get("job_type"),
            salary_text=format_salary(row),
            is_remote=None if row.get("is_remote") is None else bool(row["is_remote"]),
        )


# pandas fills empty cells with NaN/NaT and pads strings; turn all of that into
# None so the rest of the code only sees real values
def clean_cell(value: Any) -> Any:
    if isinstance(value, (list, tuple, dict)):
        return value or None
    if value is None or pd.isna(value):
        return None
    if isinstance(value, str):
        return value.strip() or None
    return value


def parse_posted_date(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError(f"unexpected posting date {value!r}")


def format_salary(row: JobSpyRow) -> str | None:
    low, high = row.get("min_amount"), row.get("max_amount")
    if low is None and high is None:
        return None
    amounts = [f"{amount:.0f}" for amount in (low, high) if amount is not None]
    amount_range = amounts[0] if len(set(amounts)) == 1 else "-".join(amounts)
    parts = [row.get("currency"), amount_range, row.get("interval")]
    return " ".join(str(part) for part in parts if part)
