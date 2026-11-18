from datetime import datetime, timezone

from job_hunt.common.settings_schema import AmazonJobsSourceSettings
from job_hunt.job_sources.html_to_text import html_to_text
from job_hunt.job_sources.job_boards.job_board_query import JobBoardQuery
from job_hunt.job_sources.job_boards.search_api_source import ApiPosting, SearchApiSource
from job_hunt.job_sources.job_source import register_job_source
from job_hunt.job_store.models import FetchedJob

# the JSON behind amazon.jobs search (robots.txt allows it; no key)
SEARCH_URL = "https://www.amazon.jobs/en/search.json"
SITE_URL = "https://www.amazon.jobs"
POSTED_DATE_FORMAT = "%B %d, %Y"


@register_job_source
class AmazonJobsSource(SearchApiSource):
    """amazon.jobs: Amazon, AWS and their UK entities. One search per role
    term per place, limited to the configured country."""

    name = "amazon_jobs"
    display_name = "Amazon Jobs"
    listing_rank = 20
    _source_settings: AmazonJobsSourceSettings

    def _check_credentials(self) -> None:
        # a public search; nothing to check
        return None

    def _fetch_page(self, query: JobBoardQuery, page_index: int) -> list[ApiPosting]:
        page_size = self._source_settings.results_per_page
        params = {
            "base_query": query.role_term,
            "loc_query": query.location,
            "country": self._source_settings.country_code,
            "result_limit": page_size,
            "offset": page_index * page_size,
        }
        return self._http_client.get_json(SEARCH_URL, params=params)["jobs"]

    def _to_fetched_job(self, raw_posting: ApiPosting) -> FetchedJob:
        return FetchedJob(
            source=self.name,
            source_job_id=str(raw_posting["id_icims"]),
            title=raw_posting["title"],
            # the hiring entity, e.g. "Amazon UK Services Ltd."
            company_name=raw_posting.get("company_name") or "Amazon",
            location=raw_posting.get("normalized_location") or raw_posting.get("location"),
            url=SITE_URL + raw_posting["job_path"],
            description=html_to_text(
                "\n".join(
                    part
                    for part in (
                        raw_posting.get("description"),
                        raw_posting.get("basic_qualifications"),
                        raw_posting.get("preferred_qualifications"),
                    )
                    if part
                )
            ),
            posted_at=parse_amazon_date(raw_posting.get("posted_date")),
            employment_type=raw_posting.get("job_schedule_type"),
        )


# "October 27, 2026"
def parse_amazon_date(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.strptime(text.strip(), POSTED_DATE_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
