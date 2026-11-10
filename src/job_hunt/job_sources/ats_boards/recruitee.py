from typing import Any
from urllib.parse import SplitResult

from job_hunt.job_sources.ats_boards.ats_board_source import AtsBoardSource, BoardPosting, company_subdomain
from job_hunt.job_sources.html_to_text import html_to_text
from job_hunt.job_sources.job_source import register_job_source
from job_hunt.job_sources.location_matching import join_locations
from job_hunt.job_sources.salary_text import format_salary_range
from job_hunt.job_sources.timestamps import parse_iso_timestamp
from job_hunt.job_store.models import AtsPlatform, FetchedJob

BOARD_DOMAIN = "recruitee.com"
BOARD_API_URL = "https://{board_slug}.recruitee.com/api/offers/"


@register_job_source
class RecruiteeSource(AtsBoardSource):
    ats_platform = AtsPlatform.RECRUITEE
    search_domains = (BOARD_DOMAIN,)

    @classmethod
    def board_slug_from_url(cls, url: SplitResult) -> str | None:
        return company_subdomain(url, BOARD_DOMAIN)

    def _fetch_board_postings(self, board_slug: str) -> list[dict[str, Any]]:
        return self._http_client.get_json(BOARD_API_URL.format(board_slug=board_slug))["offers"]

    def _posting_locations(self, posting: dict[str, Any]) -> list[str | None]:
        listed_locations = [
            ", ".join(part for part in (location.get("city"), location.get("country")) if part) or None
            for location in posting.get("locations", [])
        ]
        return [posting.get("location"), *listed_locations]

    def _to_fetched_job(self, raw_posting: BoardPosting) -> FetchedJob:
        posting = raw_posting.posting
        salary = posting.get("salary") or {}
        return FetchedJob(
            source=self.name,
            source_job_id=self._source_job_id(raw_posting.company, posting["id"]),
            title=posting["title"],
            company_name=raw_posting.company.name,
            location=join_locations(self._posting_locations(posting)),
            url=posting["careers_url"],
            employer_url=posting["careers_url"],
            # the advert is split in two: about the role, and what's asked for
            description=html_to_text(
                "\n".join(filter(None, (posting.get("description"), posting.get("requirements"))))
            ),
            posted_at=parse_iso_timestamp(posting.get("published_at") or posting.get("created_at")),
            closes_at=parse_iso_timestamp(posting.get("close_at")),
            employment_type=posting.get("employment_type_code"),
            salary_text=format_salary_range(
                salary.get("currency"), salary.get("min"), salary.get("max"), salary.get("period")
            ),
            is_remote=posting.get("remote"),
        )
