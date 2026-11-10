import base64
from datetime import datetime, timedelta, timezone

from job_hunt.common.settings_schema import ReedSourceSettings
from job_hunt.job_sources.html_to_text import html_to_text
from job_hunt.job_sources.job_boards.job_board_query import JobBoardQuery
from job_hunt.job_sources.job_boards.search_api_source import ApiPosting, SearchApiSource
from job_hunt.job_sources.job_source import UNKNOWN_COMPANY_NAME, register_job_source, require_secret
from job_hunt.job_sources.salary_text import format_salary_range
from job_hunt.job_store.models import FetchedJob, utc_now

SEARCH_URL = "https://www.reed.co.uk/api/1.0/search"
REED_DATE_FORMAT = "%d/%m/%Y"


@register_job_source
class ReedSource(SearchApiSource):
    """Reed's search API. Reed is one of the biggest UK boards and carries a
    lot of graduate schemes and agency roles the other boards don't."""

    name = "reed"
    _source_settings: ReedSourceSettings

    def _check_credentials(self) -> None:
        self._auth_header()

    # the key is the Basic auth username, with an empty password
    def _auth_header(self) -> dict[str, str]:
        api_key = require_secret(self.name, self._source_settings.api_key, "api_key")
        return {"Authorization": "Basic " + base64.b64encode(f"{api_key}:".encode()).decode()}

    def _fetch_page(self, query: JobBoardQuery, page_index: int) -> list[ApiPosting]:
        page_size = self._source_settings.results_per_page
        params = {
            "keywords": query.role_term,
            "locationName": query.location,
            "distanceFromLocation": self._search_settings.search_radius_miles,
            "resultsToTake": page_size,
            "resultsToSkip": page_index * page_size,
        }
        response = self._http_client.get_json(SEARCH_URL, params=params, headers=self._auth_header())
        return response.get("results", [])

    # Reed's search has no "posted since" filter, so old adverts are dropped here
    def _is_in_scope(self, raw_posting: ApiPosting) -> bool:
        posted_at = parse_reed_date(raw_posting.get("date"))
        max_age = timedelta(hours=self._search_settings.max_posting_age_hours)
        return posted_at is None or utc_now() - posted_at <= max_age

    def _to_fetched_job(self, raw_posting: ApiPosting) -> FetchedJob:
        return FetchedJob(
            source=self.name,
            source_job_id=str(raw_posting["jobId"]),
            title=raw_posting["jobTitle"],
            company_name=(raw_posting.get("employerName") or "").strip() or UNKNOWN_COMPANY_NAME,
            location=raw_posting.get("locationName"),
            url=raw_posting["jobUrl"],
            # a snippet; the full advert needs one more request per job
            description=html_to_text(raw_posting.get("jobDescription")),
            posted_at=parse_reed_date(raw_posting.get("date")),
            salary_text=format_salary_range(
                raw_posting.get("currency"),
                raw_posting.get("minimumSalary"),
                raw_posting.get("maximumSalary"),
                None,
            ),
        )


# Reed gives dates only, as dd/mm/yyyy
def parse_reed_date(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.strptime(text, REED_DATE_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
