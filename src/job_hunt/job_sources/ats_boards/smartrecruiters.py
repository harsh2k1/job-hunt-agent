from typing import Any
from urllib.parse import SplitResult

from job_hunt.common.settings_schema import SmartRecruitersSourceSettings
from job_hunt.job_sources.ats_boards.ats_board_source import AtsBoardSource, BoardPosting, first_path_segment
from job_hunt.job_sources.job_source import register_job_source
from job_hunt.job_sources.location_matching import join_locations
from job_hunt.job_store.models import AtsPlatform, FetchedJob

BOARD_HOSTS = frozenset({"jobs.smartrecruiters.com", "careers.smartrecruiters.com"})
BOARD_API_URL = "https://api.smartrecruiters.com/v1/companies/{board_slug}/postings"
POSTING_PAGE_URL = "https://jobs.smartrecruiters.com/{board_slug}/{posting_id}"


@register_job_source
class SmartRecruitersSource(AtsBoardSource):
    """The listing has no advert text (that would be one more request per
    job), so descriptions are left empty; filtering can fetch them for the
    jobs worth reading."""

    ats_platform = AtsPlatform.SMARTRECRUITERS
    display_name = "SmartRecruiters"
    search_domains = ("jobs.smartrecruiters.com",)
    _source_settings: SmartRecruitersSourceSettings

    @classmethod
    def board_slug_from_url(cls, url: SplitResult) -> str | None:
        return first_path_segment(url) if url.hostname in BOARD_HOSTS else None

    # Paged, and filtered to one country server-side: big employers list
    # thousands of postings worldwide.
    def _fetch_board_postings(self, board_slug: str) -> list[dict[str, Any]]:
        postings: list[dict[str, Any]] = []
        while True:
            page = self._http_client.get_json(
                BOARD_API_URL.format(board_slug=board_slug),
                params={
                    "country": self._source_settings.country_code,
                    "limit": self._source_settings.page_size,
                    "offset": len(postings),
                },
            )
            postings.extend(page["content"])
            if not page["content"] or len(postings) >= page["totalFound"]:
                return postings

    def _posting_locations(self, posting: dict[str, Any]) -> list[str | None]:
        location = posting.get("location") or {}
        if location.get("fullLocation"):
            return [location["fullLocation"]]
        parts = [location.get("city"), location.get("region"), location.get("country")]
        return [", ".join(part for part in parts if part) or None]

    def _to_fetched_job(self, raw_posting: BoardPosting) -> FetchedJob:
        posting = raw_posting.posting
        board_slug = raw_posting.company.ats_board_slug
        posting_page_url = POSTING_PAGE_URL.format(board_slug=board_slug, posting_id=posting["id"])
        return FetchedJob(
            source=self.name,
            source_job_id=self._source_job_id(raw_posting.company, posting["id"]),
            title=posting["name"],
            company_name=(posting.get("company") or {}).get("name") or raw_posting.company.name,
            location=join_locations(self._posting_locations(posting)),
            url=posting_page_url,
            employer_url=posting_page_url,
            posted_at=posting.get("releasedDate"),
            employment_type=(posting.get("typeOfEmployment") or {}).get("label"),
            is_remote=(posting.get("location") or {}).get("remote"),
        )
