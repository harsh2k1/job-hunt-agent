from typing import Any
from urllib.parse import SplitResult
from xml.etree import ElementTree

from job_hunt.job_sources.ats_boards.ats_board_source import AtsBoardSource, BoardPosting, company_subdomain
from job_hunt.job_sources.html_to_text import html_to_text
from job_hunt.job_sources.job_source import register_job_source
from job_hunt.job_sources.location_matching import join_locations
from job_hunt.job_sources.timestamps import parse_iso_timestamp
from job_hunt.job_store.models import AtsPlatform, FetchedJob

# every company board is on both; the feed answers on either
BOARD_DOMAINS = ("jobs.personio.de", "jobs.personio.com")
BOARD_FEED_URL = "https://{board_slug}.jobs.personio.de/xml"
POSTING_PAGE_URL = "https://{board_slug}.jobs.personio.de/job/{posting_id}?language=en"


@register_job_source
class PersonioSource(AtsBoardSource):
    """Personio publishes each board as one XML feed. Postings are turned
    into dicts here so the rest of the source reads like the JSON ATSs."""

    ats_platform = AtsPlatform.PERSONIO
    display_name = "Personio"
    search_domains = BOARD_DOMAINS

    @classmethod
    def board_slug_from_url(cls, url: SplitResult) -> str | None:
        return next(filter(None, (company_subdomain(url, domain) for domain in BOARD_DOMAINS)), None)

    def _fetch_board_postings(self, board_slug: str) -> list[dict[str, Any]]:
        feed = ElementTree.fromstring(
            self._http_client.get_text(BOARD_FEED_URL.format(board_slug=board_slug))
        )
        return [
            read_personio_position(position) | {"board_slug": board_slug}
            for position in feed.iter("position")
        ]

    def _posting_locations(self, posting: dict[str, Any]) -> list[str | None]:
        return posting["offices"]

    def _to_fetched_job(self, raw_posting: BoardPosting) -> FetchedJob:
        posting = raw_posting.posting
        page_url = POSTING_PAGE_URL.format(board_slug=posting["board_slug"], posting_id=posting["id"])
        return FetchedJob(
            source=self.name,
            source_job_id=self._source_job_id(raw_posting.company, posting["id"]),
            title=posting["name"],
            company_name=raw_posting.company.name,
            location=join_locations(posting["offices"]),
            url=page_url,
            employer_url=page_url,
            description=html_to_text(posting["description_html"]),
            posted_at=parse_iso_timestamp(posting.get("createdAt")),
            employment_type=", ".join(filter(None, (posting.get("employmentType"), posting.get("schedule"))))
            or None,
            is_remote=True if any(office.lower() == "remote" for office in posting["offices"]) else None,
        )


def read_personio_position(position: ElementTree.Element) -> dict[str, Any]:
    offices = [
        position.findtext("office"),
        *(office.text for office in position.iterfind("additionalOffices/office")),
    ]
    sections = [
        f"<h3>{section.findtext('name', '')}</h3>{section.findtext('value', '')}"
        for section in position.iterfind("jobDescriptions/jobDescription")
    ]
    return {
        "id": position.findtext("id"),
        "name": (position.findtext("name") or "").strip(),
        "offices": [office.strip() for office in offices if office and office.strip()],
        "description_html": "".join(sections) or None,
        "createdAt": position.findtext("createdAt"),
        "employmentType": position.findtext("employmentType"),
        "schedule": position.findtext("schedule"),
    }
