from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import SplitResult
from xml.etree import ElementTree

from job_hunt.job_sources.ats_boards.ats_board_source import AtsBoardSource, BoardPosting, company_subdomain
from job_hunt.job_sources.html_to_text import html_to_text
from job_hunt.job_sources.job_source import register_job_source
from job_hunt.job_sources.location_matching import join_locations
from job_hunt.job_store.models import AtsPlatform, FetchedJob

BOARD_DOMAIN = "teamtailor.com"
# the public RSS feed of a careers site; robots.txt allows it
BOARD_FEED_URL = "https://{board_slug}.teamtailor.com/jobs.rss"
LOCATION_NAMESPACE = {"tt": "https://teamtailor.com/locations"}


@register_job_source
class TeamtailorSource(AtsBoardSource):
    """Teamtailor careers sites publish their openings as RSS. Items are
    turned into dicts here so the rest reads like the JSON ATSs. Companies on
    their own domain (careers.example.com) can't be recognised from a link."""

    ats_platform = AtsPlatform.TEAMTAILOR
    search_domains = (BOARD_DOMAIN,)

    @classmethod
    def board_slug_from_url(cls, url: SplitResult) -> str | None:
        return company_subdomain(url, BOARD_DOMAIN)

    def _fetch_board_postings(self, board_slug: str) -> list[dict[str, Any]]:
        feed = ElementTree.fromstring(
            self._http_client.get_text(BOARD_FEED_URL.format(board_slug=board_slug))
        )
        return [read_teamtailor_item(item) for item in feed.iter("item")]

    def _posting_locations(self, posting: dict[str, Any]) -> list[str | None]:
        return posting["locations"]

    def _to_fetched_job(self, raw_posting: BoardPosting) -> FetchedJob:
        posting = raw_posting.posting
        return FetchedJob(
            source=self.name,
            source_job_id=self._source_job_id(raw_posting.company, posting["guid"]),
            title=posting["title"],
            company_name=raw_posting.company.name,
            location=join_locations(posting["locations"]),
            url=posting["link"],
            employer_url=posting["link"],
            description=html_to_text(posting["description"]),
            posted_at=parsedate_to_datetime(posting["pubDate"]) if posting.get("pubDate") else None,
            is_remote=True if posting.get("remoteStatus") in ("fully", "remote") else None,
        )


def read_teamtailor_item(item: ElementTree.Element) -> dict[str, Any]:
    locations = [
        ", ".join(
            part.strip()
            for part in (
                location.findtext("tt:city", "", LOCATION_NAMESPACE),
                location.findtext("tt:country", "", LOCATION_NAMESPACE),
            )
            if part.strip()
        )
        or location.findtext("tt:name", None, LOCATION_NAMESPACE)
        for location in item.iterfind("tt:locations/tt:location", LOCATION_NAMESPACE)
    ]
    return {
        "guid": item.findtext("guid") or item.findtext("link"),
        "title": (item.findtext("title") or "").strip(),
        "link": item.findtext("link"),
        "description": item.findtext("description"),
        "pubDate": item.findtext("pubDate"),
        "remoteStatus": item.findtext("remoteStatus"),
        "locations": [location for location in locations if location],
    }
