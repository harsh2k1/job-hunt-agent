from typing import Any
from urllib.parse import SplitResult, parse_qs

from job_hunt.job_sources.ats_boards.ats_board_source import AtsBoardSource, BoardPosting, first_path_segment
from job_hunt.job_sources.html_to_text import html_to_text
from job_hunt.job_sources.job_source import register_job_source
from job_hunt.job_sources.location_matching import join_locations
from job_hunt.job_store.models import AtsPlatform, FetchedJob

BOARD_HOSTS = frozenset({"boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io"})
# EU-hosted boards are served by the same API (there is no EU API host)
BOARD_API_URL = "https://boards-api.greenhouse.io/v1/boards/{board_slug}/jobs"
EMBEDDED_BOARD_PATH = "/embed/job_board"


@register_job_source
class GreenhouseSource(AtsBoardSource):
    ats_platform = AtsPlatform.GREENHOUSE
    search_domains = tuple(sorted(BOARD_HOSTS))

    @classmethod
    def board_slug_from_url(cls, url: SplitResult) -> str | None:
        if url.hostname not in BOARD_HOSTS:
            return None
        # boards embedded on a company's own site: /embed/job_board?for=<slug>
        if url.path.startswith(EMBEDDED_BOARD_PATH):
            return next(iter(parse_qs(url.query).get("for", [])), None)
        return first_path_segment(url)

    def _fetch_board_postings(self, board_slug: str) -> list[dict[str, Any]]:
        # content=true adds the advert text, saving a request per job
        response = self._http_client.get_json(
            BOARD_API_URL.format(board_slug=board_slug), params={"content": "true"}
        )
        return response["jobs"]

    def _posting_locations(self, posting: dict[str, Any]) -> list[str | None]:
        office_locations = [
            office.get("location") or office.get("name") for office in posting.get("offices", [])
        ]
        return [(posting.get("location") or {}).get("name"), *office_locations]

    def _to_fetched_job(self, raw_posting: BoardPosting) -> FetchedJob:
        posting = raw_posting.posting
        return FetchedJob(
            source=self.name,
            source_job_id=self._source_job_id(raw_posting.company, posting["id"]),
            title=posting["title"],
            company_name=posting.get("company_name") or raw_posting.company.name,
            location=join_locations(self._posting_locations(posting)),
            url=posting["absolute_url"],
            employer_url=posting["absolute_url"],
            # Greenhouse sends the advert HTML escaped a second time
            description=html_to_text(posting.get("content"), is_escaped=True),
            posted_at=posting.get("first_published") or posting.get("updated_at"),
        )
