from typing import Any
from urllib.parse import SplitResult

from job_hunt.job_sources.ats_boards.ats_board_source import AtsBoardSource, BoardPosting, company_subdomain
from job_hunt.job_sources.job_source import register_job_source
from job_hunt.job_sources.location_matching import join_locations
from job_hunt.job_store.models import AtsPlatform, FetchedJob

BOARD_DOMAIN = "bamboohr.com"
BOARD_API_URL = "https://{board_slug}.bamboohr.com/careers/list"
POSTING_PAGE_URL = "https://{board_slug}.bamboohr.com/careers/{posting_id}"


@register_job_source
class BambooHrSource(AtsBoardSource):
    """The careers list has titles and places only: no advert text and no
    dates (each is one more request per job). Filtering can fetch the
    adverts worth reading."""

    ats_platform = AtsPlatform.BAMBOOHR
    search_domains = (BOARD_DOMAIN,)

    @classmethod
    def board_slug_from_url(cls, url: SplitResult) -> str | None:
        # careers pages live under /careers; other paths are BambooHR's own app
        if not url.path.startswith("/careers"):
            return None
        return company_subdomain(url, BOARD_DOMAIN)

    def _fetch_board_postings(self, board_slug: str) -> list[dict[str, Any]]:
        postings = self._http_client.get_json(BOARD_API_URL.format(board_slug=board_slug))["result"]
        return [posting | {"board_slug": board_slug} for posting in postings]

    def _posting_locations(self, posting: dict[str, Any]) -> list[str | None]:
        return [format_place(posting.get("location")), format_place(posting.get("atsLocation"))]

    def _to_fetched_job(self, raw_posting: BoardPosting) -> FetchedJob:
        posting = raw_posting.posting
        page_url = POSTING_PAGE_URL.format(board_slug=posting["board_slug"], posting_id=posting["id"])
        return FetchedJob(
            source=self.name,
            source_job_id=self._source_job_id(raw_posting.company, posting["id"]),
            title=posting["jobOpeningName"].strip(),
            company_name=raw_posting.company.name,
            location=join_locations(self._posting_locations(posting)),
            url=page_url,
            employer_url=page_url,
            employment_type=posting.get("employmentStatusLabel"),
            is_remote=posting.get("isRemote"),
        )


def format_place(place: dict[str, Any] | None) -> str | None:
    if not place:
        return None
    parts = (place.get("city"), place.get("state") or place.get("province"), place.get("country"))
    return ", ".join(dict.fromkeys(part for part in parts if part)) or None
