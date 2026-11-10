from datetime import datetime, timezone
from typing import Any, ClassVar
from urllib.parse import SplitResult

from job_hunt.job_sources.ats_boards.ats_board_source import AtsBoardSource, BoardPosting, first_path_segment
from job_hunt.job_sources.html_to_text import html_to_text
from job_hunt.job_sources.job_source import register_job_source
from job_hunt.job_sources.location_matching import join_locations
from job_hunt.job_store.models import AtsPlatform, FetchedJob

REMOTE_WORKPLACE_TYPE = "remote"


@register_job_source
class LeverSource(AtsBoardSource):
    ats_platform = AtsPlatform.LEVER
    board_host: ClassVar[str] = "jobs.lever.co"
    search_domains = ("jobs.lever.co",)
    board_api_url: ClassVar[str] = "https://api.lever.co/v0/postings/{board_slug}"

    @classmethod
    def board_slug_from_url(cls, url: SplitResult) -> str | None:
        return first_path_segment(url) if url.hostname == cls.board_host else None

    def _fetch_board_postings(self, board_slug: str) -> list[dict[str, Any]]:
        # without mode=json Lever answers with an HTML page
        return self._http_client.get_json(
            self.board_api_url.format(board_slug=board_slug), params={"mode": "json"}
        )

    def _posting_locations(self, posting: dict[str, Any]) -> list[str | None]:
        categories = posting.get("categories") or {}
        return [categories.get("location"), *categories.get("allLocations", [])]

    def _to_fetched_job(self, raw_posting: BoardPosting) -> FetchedJob:
        posting = raw_posting.posting
        categories = posting.get("categories") or {}
        workplace_type = posting.get("workplaceType")
        return FetchedJob(
            source=self.name,
            source_job_id=self._source_job_id(raw_posting.company, posting["id"]),
            title=posting["text"],
            company_name=raw_posting.company.name,
            location=join_locations(self._posting_locations(posting)),
            url=posting["hostedUrl"],
            employer_url=posting["hostedUrl"],
            description=build_lever_description(posting),
            # milliseconds since the epoch
            posted_at=datetime.fromtimestamp(posting["createdAt"] / 1000, tz=timezone.utc),
            employment_type=categories.get("commitment"),
            is_remote=None if workplace_type is None else workplace_type == REMOTE_WORKPLACE_TYPE,
        )


@register_job_source
class LeverEuSource(LeverSource):
    ats_platform = AtsPlatform.LEVER_EU
    board_host = "jobs.eu.lever.co"
    search_domains = ("jobs.eu.lever.co",)
    board_api_url = "https://api.eu.lever.co/v0/postings/{board_slug}"


# Lever splits an advert into an opening, titled lists (responsibilities,
# requirements, ...) and a closing section; put them back together in order.
def build_lever_description(posting: dict[str, Any]) -> str | None:
    sections = [posting.get("descriptionPlain")]
    for titled_list in posting.get("lists", []):
        list_text = html_to_text(titled_list.get("content"))
        sections.append("\n".join(part for part in (titled_list.get("text"), list_text) if part))
    sections.append(posting.get("additionalPlain"))
    return "\n\n".join(section.strip() for section in sections if section and section.strip()) or None
