"""Gradcracker, read from search result pages saved in a browser.

Gradcracker answers every automated request with a bot check, so it isn't
fetched. Its result pages (e.g. Data Science graduate jobs, sorted by date)
are saved from a browser into one folder per day, named for the day
(17-11-2026), and read from there. A folder counts as seen on its date, so
a job's first and last sightings are the days pages showing it were saved.

A card has the title, employer, a subtitle (disciplines, or the dates of an
insight programme), deadline, salary, location, degree, start and duration,
but no advert text: those facts are what the screening reads. Pages also
carry a hidden decoy card for scrapers (hub 9000001, disallowed in
robots.txt); only real result cards (data-search-result) are read.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from job_hunt.common.logger import get_logger
from job_hunt.common.settings_schema import GradcrackerSourceSettings, Settings
from job_hunt.job_sources.job_source import UNKNOWN_COMPANY_NAME, JobSource, register_job_source
from job_hunt.job_store.models import FetchedJob

logger = get_logger(__name__)

UK_TIME = ZoneInfo("Europe/London")
# hub id, employer slug, opportunity type, job id
ADVERT_URL = re.compile(r"^https://www\.gradcracker\.com/hub/(\d+)/([^/]+)/([a-z-]+)/(\d+)/")
DECOY_HUB = "9000001"
OPPORTUNITY_TYPES = {
    "graduate-job": "Graduate job",
    "work-placement-internship": "Placement or internship",
    "degree-apprenticeship": "Degree apprenticeship",
}
# "December 13th, 2026", "5pm, November 28th, 2026", "Today", "Tomorrow"
# (the day the page was saved, or the next); "Ongoing" has no date
DEADLINE = re.compile(
    r"^(?:(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>am|pm),\s*)?"
    r"(?:(?P<relative>today|tomorrow)"
    r"|(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?,\s*(?P<year>\d{4}))$",
    re.IGNORECASE,
)
# careers events are listed among placements, but aren't roles
CAREERS_EVENT = re.compile(r"\b(?:webinar|information session|open (?:evening|day))\b", re.IGNORECASE)
FOLDER_DATE_FORMATS = ("%d-%m-%Y", "%Y-%m-%d")
# a saved page counts as seen at midday on its folder's day
SEEN_AT = time(12, 0)


@dataclass(frozen=True)
class GradcrackerCard:
    job_id: str
    title: str
    url: str
    employer: str
    opportunity: str
    summary: str
    # the day the page was saved: "Today" on the card means this day
    saved_on: date
    # label -> value, as the card shows them: Deadline, Salary, Location, ...
    facts: dict[str, str] = field(default_factory=dict)


def read_result_cards(page_html: str, saved_on: date) -> list[GradcrackerCard]:
    soup = BeautifulSoup(page_html, "html.parser")
    cards = []
    for article in soup.find_all("article", attrs={"data-search-result": True}):
        heading = article.find("h2")
        link = heading.find("a", href=True) if heading else None
        url = str(link["href"]) if link else ""
        match = ADVERT_URL.match(url)
        if link is None or match is None or match.group(1) == DECOY_HUB:
            continue
        _, employer_slug, opportunity, job_id = match.groups()
        logo = article.find("img", alt=True)
        employer = str(logo["alt"]).strip() if logo else ""
        subtitle = article.find("h3")
        facts = {}
        for label in article.find_all("dt"):
            value = label.find_next_sibling("dd")
            if value is not None:
                facts[label.get_text(" ", strip=True)] = value.get_text(" ", strip=True)
        cards.append(
            GradcrackerCard(
                job_id=job_id,
                title=link.get_text(" ", strip=True),
                url=url,
                employer=employer or employer_slug.replace("-", " ").title(),
                opportunity=OPPORTUNITY_TYPES.get(opportunity, opportunity.replace("-", " ").capitalize()),
                summary=subtitle.get_text(" ", strip=True) if subtitle else "",
                saved_on=saved_on,
                facts=facts,
            )
        )
    return cards


# None for "Ongoing" or anything unreadable; a deadline without a time is
# taken as the end of that day, UK time
def parse_deadline(text: str | None, saved_on: date) -> datetime | None:
    match = DEADLINE.match((text or "").strip())
    if match is None:
        return None
    if match["relative"]:
        day = saved_on + timedelta(days=1 if match["relative"].lower() == "tomorrow" else 0)
    else:
        try:
            day = datetime.strptime(f"{match['day']} {match['month']} {match['year']}", "%d %B %Y").date()
        except ValueError:
            return None
    if match["hour"] is None:
        return datetime.combine(day, time(23, 59), UK_TIME)
    hour = int(match["hour"]) % 12 + (12 if match["meridiem"].lower() == "pm" else 0)
    return datetime.combine(day, time(hour, int(match["minute"] or 0)), UK_TIME)


def folder_date(folder: Path) -> date | None:
    for date_format in FOLDER_DATE_FORMATS:
        try:
            return datetime.strptime(folder.name, date_format).date()
        except ValueError:
            continue
    return None


def advert_text(card: GradcrackerCard) -> str:
    lines = [f"Gradcracker listing: {card.opportunity}"]
    if card.summary:
        lines.append(card.summary)
    lines += [f"{label}: {value}" for label, value in card.facts.items()]
    return "\n".join(lines)


@register_job_source
class GradcrackerSource(JobSource[GradcrackerCard]):
    name = "gradcracker"
    display_name = "Gradcracker"
    # no advert text: another source's copy of the same job is shown first
    listing_rank = 65

    def __init__(self, source_settings: GradcrackerSourceSettings):
        self._source_settings = source_settings

    @classmethod
    def from_settings(cls, settings: Settings) -> "GradcrackerSource":
        return cls(settings.job_sources.gradcracker)

    @classmethod
    def unseen_jobs_listed_since(cls, settings: Settings, now: datetime) -> datetime | None:
        return now - timedelta(days=settings.job_sources.gradcracker.listed_without_deadline_days)

    # one batch per day folder, oldest first, each seen on its folder's day
    def fetch_job_batches(self) -> list[tuple[datetime | None, list[FetchedJob]]]:
        batches: list[tuple[datetime | None, list[FetchedJob]]] = []
        for day, folder in self._day_folders():
            seen_at = datetime.combine(day, SEEN_AT, UK_TIME)
            batches.append((seen_at, self._read_postings(self._cards_in(folder, day))))
        return batches

    def _list_raw_postings(self) -> Iterable[GradcrackerCard]:
        for day, folder in self._day_folders():
            yield from self._cards_in(folder, day)

    def _is_in_scope(self, raw_posting: GradcrackerCard) -> bool:
        return CAREERS_EVENT.search(raw_posting.title) is None

    def _to_fetched_job(self, raw_posting: GradcrackerCard) -> FetchedJob:
        return FetchedJob(
            source=self.name,
            source_job_id=raw_posting.job_id,
            title=raw_posting.title,
            company_name=raw_posting.employer or UNKNOWN_COMPANY_NAME,
            location=raw_posting.facts.get("Location"),
            url=raw_posting.url,
            description=advert_text(raw_posting),
            employment_type=raw_posting.opportunity,
            salary_text=raw_posting.facts.get("Salary"),
            closes_at=parse_deadline(raw_posting.facts.get("Deadline"), raw_posting.saved_on),
        )

    def _day_folders(self) -> list[tuple[date, Path]]:
        pages_dir = self._source_settings.pages_dir
        if not pages_dir.is_dir():
            logger.warning("gradcracker: no saved pages folder at %s", pages_dir)
            return []
        folders = []
        for folder in pages_dir.iterdir():
            if not folder.is_dir():
                continue
            day = folder_date(folder)
            if day is None:
                logger.warning("gradcracker: skipped %s, not named for a day (17-11-2026)", folder.name)
                continue
            folders.append((day, folder))
        return sorted(folders)

    # a job on several pages of the same day (AI and Data Science) counts once
    @staticmethod
    def _cards_in(folder: Path, day: date) -> list[GradcrackerCard]:
        cards: dict[str, GradcrackerCard] = {}
        for page in sorted(folder.glob("*.html")):
            page_cards = read_result_cards(page.read_text(errors="replace"), day)
            if not page_cards:
                logger.warning("gradcracker: no job cards in %s (not a result page?)", page)
            for card in page_cards:
                cards.setdefault(card.job_id, card)
        return list(cards.values())
