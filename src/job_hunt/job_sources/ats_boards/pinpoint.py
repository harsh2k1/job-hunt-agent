from typing import Any
from urllib.parse import SplitResult

from job_hunt.job_sources.ats_boards.ats_board_source import AtsBoardSource, BoardPosting, company_subdomain
from job_hunt.job_sources.html_to_text import html_to_text
from job_hunt.job_sources.job_source import register_job_source
from job_hunt.job_sources.location_matching import join_locations
from job_hunt.job_sources.salary_text import format_salary_range
from job_hunt.job_sources.timestamps import parse_iso_timestamp
from job_hunt.job_store.models import AtsPlatform, FetchedJob

BOARD_DOMAIN = "pinpointhq.com"
BOARD_API_URL = "https://{board_slug}.pinpointhq.com/postings.json"
# the advert is split into sections, each with its own field
DESCRIPTION_FIELDS = ("description", "key_responsibilities", "skills_knowledge_expertise", "benefits")


@register_job_source
class PinpointSource(AtsBoardSource):
    ats_platform = AtsPlatform.PINPOINT
    search_domains = (BOARD_DOMAIN,)

    @classmethod
    def board_slug_from_url(cls, url: SplitResult) -> str | None:
        return company_subdomain(url, BOARD_DOMAIN)

    def _fetch_board_postings(self, board_slug: str) -> list[dict[str, Any]]:
        return self._http_client.get_json(BOARD_API_URL.format(board_slug=board_slug))["data"]

    # "UK - London" carries the country; the city alone often doesn't
    def _posting_locations(self, posting: dict[str, Any]) -> list[str | None]:
        location = posting.get("location") or {}
        return [location.get("name"), location.get("city")]

    def _to_fetched_job(self, raw_posting: BoardPosting) -> FetchedJob:
        posting = raw_posting.posting
        salary_parts = (
            (
                posting.get("compensation_currency"),
                posting.get("compensation_minimum"),
                posting.get("compensation_maximum"),
                posting.get("compensation_frequency"),
            )
            if posting.get("compensation_visible")
            else (None, None, None, None)
        )
        return FetchedJob(
            source=self.name,
            source_job_id=self._source_job_id(raw_posting.company, posting["id"]),
            title=posting["title"],
            company_name=raw_posting.company.name,
            location=join_locations(self._posting_locations(posting)),
            url=posting["url"],
            employer_url=posting["url"],
            description=html_to_text("\n".join(filter(None, map(posting.get, DESCRIPTION_FIELDS)))),
            closes_at=parse_iso_timestamp(posting.get("deadline_at")),
            employment_type=posting.get("employment_type_text"),
            salary_text=format_salary_range(*salary_parts),
            is_remote=True if posting.get("workplace_type") == "remote" else None,
        )
