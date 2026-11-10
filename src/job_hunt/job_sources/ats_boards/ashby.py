from typing import Any
from urllib.parse import SplitResult

from job_hunt.job_sources.ats_boards.ats_board_source import AtsBoardSource, BoardPosting, first_path_segment
from job_hunt.job_sources.job_source import register_job_source
from job_hunt.job_sources.location_matching import join_locations
from job_hunt.job_store.models import AtsPlatform, FetchedJob

BOARD_HOST = "jobs.ashbyhq.com"
BOARD_API_URL = "https://api.ashbyhq.com/posting-api/job-board/{board_slug}"


@register_job_source
class AshbySource(AtsBoardSource):
    ats_platform = AtsPlatform.ASHBY
    search_domains = (BOARD_HOST,)

    @classmethod
    def board_slug_from_url(cls, url: SplitResult) -> str | None:
        return first_path_segment(url) if url.hostname == BOARD_HOST else None

    def _fetch_board_postings(self, board_slug: str) -> list[dict[str, Any]]:
        postings = self._http_client.get_json(BOARD_API_URL.format(board_slug=board_slug))["jobs"]
        # unlisted postings are reachable by link only, not meant to be applied to
        return [posting for posting in postings if posting.get("isListed", True)]

    def _posting_locations(self, posting: dict[str, Any]) -> list[str | None]:
        secondary_locations = [
            secondary.get("location") for secondary in posting.get("secondaryLocations", [])
        ]
        return [posting.get("location"), *secondary_locations]

    def _to_fetched_job(self, raw_posting: BoardPosting) -> FetchedJob:
        posting = raw_posting.posting
        return FetchedJob(
            source=self.name,
            source_job_id=self._source_job_id(raw_posting.company, posting["id"]),
            title=posting["title"],
            company_name=raw_posting.company.name,
            location=join_locations(self._posting_locations(posting)),
            url=posting["jobUrl"],
            employer_url=posting["jobUrl"],
            description=posting.get("descriptionPlain"),
            posted_at=posting.get("publishedAt"),
            employment_type=posting.get("employmentType"),
            is_remote=posting.get("isRemote"),
        )
