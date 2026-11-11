from datetime import datetime, timezone

from job_hunt.job_sources.job_boards.job_board_query import JobBoardQuery
from job_hunt.job_sources.job_boards.search_api_source import ApiPosting, SearchApiSource
from job_hunt.job_sources.job_source import register_job_source
from job_hunt.job_sources.location_matching import join_locations
from job_hunt.job_store.models import FetchedJob

# Microsoft's careers search, which its robots.txt allows (/api/pcsx)
SEARCH_URL = "https://apply.careers.microsoft.com/api/pcsx/search"
JOB_PAGE_URL = "https://apply.careers.microsoft.com/careers/job/{job_id}"
REMOTE_WORK_OPTION = "remote"


@register_job_source
class MicrosoftCareersSource(SearchApiSource):
    """Microsoft's own careers search: one search per role term per place,
    pages of ten (the API's fixed page size). No key needed."""

    name = "microsoft_careers"
    listing_rank = 20

    def _check_credentials(self) -> None:
        # a public search; nothing to check
        return None

    def _fetch_page(self, query: JobBoardQuery, page_index: int) -> list[ApiPosting]:
        params = {
            "domain": "microsoft.com",
            "query": query.role_term,
            "location": query.location,
            "start": page_index * self._source_settings.results_per_page,
        }
        return self._http_client.get_json(SEARCH_URL, params=params)["data"]["positions"]

    def _to_fetched_job(self, raw_posting: ApiPosting) -> FetchedJob:
        posted_seconds = raw_posting.get("postedTs") or raw_posting.get("creationTs")
        return FetchedJob(
            source=self.name,
            source_job_id=str(raw_posting["id"]),
            title=raw_posting["name"],
            company_name="Microsoft",
            location=join_locations(
                raw_posting.get("locations") or raw_posting.get("standardizedLocations") or []
            ),
            url=JOB_PAGE_URL.format(job_id=raw_posting["id"]),
            posted_at=datetime.fromtimestamp(posted_seconds, tz=timezone.utc) if posted_seconds else None,
            employment_type=raw_posting.get("department"),
            is_remote=True if raw_posting.get("workLocationOption") == REMOTE_WORK_OPTION else None,
        )
