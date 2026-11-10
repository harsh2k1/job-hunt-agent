from typing import Any
from urllib.parse import SplitResult

from job_hunt.job_sources.ats_boards.ats_board_source import AtsBoardSource, BoardPosting, first_path_segment
from job_hunt.job_sources.html_to_text import html_to_text
from job_hunt.job_sources.job_source import register_job_source
from job_hunt.job_sources.location_matching import join_locations
from job_hunt.job_store.models import AtsPlatform, FetchedJob

BOARD_HOST = "apply.workable.com"
# details=true adds the advert text, saving a request per job
BOARD_API_URL = "https://apply.workable.com/api/v1/widget/accounts/{board_slug}"
# apply.workable.com/j/<code> and /api/... carry no company slug
NON_BOARD_PATH_SEGMENTS = frozenset({"j", "api"})


@register_job_source
class WorkableSource(AtsBoardSource):
    ats_platform = AtsPlatform.WORKABLE
    search_domains = (BOARD_HOST,)

    @classmethod
    def board_slug_from_url(cls, url: SplitResult) -> str | None:
        if url.hostname != BOARD_HOST:
            return None
        segment = first_path_segment(url)
        return segment if segment not in NON_BOARD_PATH_SEGMENTS else None

    def _fetch_board_postings(self, board_slug: str) -> list[dict[str, Any]]:
        return self._http_client.get_json(
            BOARD_API_URL.format(board_slug=board_slug), params={"details": "true"}
        )["jobs"]

    def _posting_locations(self, posting: dict[str, Any]) -> list[str | None]:
        listed_locations = [
            format_workable_location(location)
            for location in posting.get("locations", [])
            if not location.get("hidden")
        ]
        return [format_workable_location(posting), *listed_locations]

    def _to_fetched_job(self, raw_posting: BoardPosting) -> FetchedJob:
        posting = raw_posting.posting
        return FetchedJob(
            source=self.name,
            source_job_id=self._source_job_id(raw_posting.company, posting["shortcode"]),
            title=posting["title"],
            company_name=raw_posting.company.name,
            location=join_locations(self._posting_locations(posting)),
            url=posting["url"],
            employer_url=posting["url"],
            description=html_to_text(posting.get("description")),
            posted_at=posting.get("published_on") or posting.get("created_at"),
            employment_type=posting.get("employment_type") or None,
            is_remote=posting.get("telecommuting"),
        )


# Workable gives city / region / country separately; the posting itself uses
# "state", its locations list uses "region"
def format_workable_location(location: dict[str, Any]) -> str | None:
    parts = [location.get("city"), location.get("state") or location.get("region"), location.get("country")]
    return ", ".join(part for part in parts if part) or None
