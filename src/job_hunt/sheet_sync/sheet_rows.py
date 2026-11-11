"""What each tab of the job sheet shows, and which cells belong to the user.

Jobs: listings kept by the screening, soonest deadline first. Status and
Notes are the user's and survive every sync; so do rows the user adds
(no listing key), which say "self" under Found by.
Skipped: everything screened out, with the reason, so nothing vanishes.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from job_hunt.job_pipeline.listings import Listing
from job_hunt.job_store.sponsor_repository import SponsorRepository

USER_COLUMNS = ("Status", "Notes")
KEY_COLUMN = "Listing key"
FOUND_BY_AGENT = "agent"


@dataclass(frozen=True)
class SheetColumn:
    header: str
    value: Callable[[Listing], str]


@dataclass(frozen=True)
class ScreenedListing:
    listing: Listing
    # None when kept
    skip_reason: str | None


def _date(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d") if value else ""


def _first_fetched(listing: Listing) -> datetime:
    return min(job.first_seen_at for job in listing.sightings)


def jobs_columns(sponsor_status: Callable[[str], str]) -> list[SheetColumn]:
    return [
        SheetColumn("Title", lambda listing: listing.primary.title),
        SheetColumn("Company", lambda listing: listing.primary.company_name),
        SheetColumn("Location", lambda listing: listing.primary.location or ""),
        SheetColumn("Closes", lambda listing: _date(listing.closes_at)),
        SheetColumn("Posted", lambda listing: _date(listing.primary.posted_at)),
        SheetColumn("Type", lambda listing: listing.primary.employment_type or ""),
        SheetColumn("Salary", lambda listing: listing.primary.salary_text or ""),
        SheetColumn("Sponsor licence", lambda listing: sponsor_status(listing.primary.company_name)),
        SheetColumn("Found on", lambda listing: ", ".join(listing.sources)),
        SheetColumn("First fetched", lambda listing: _date(_first_fetched(listing))),
        SheetColumn("Found by", lambda listing: FOUND_BY_AGENT),
        SheetColumn("Link", lambda listing: listing.primary.url),
    ]


SKIPPED_HEADER = [
    "Title",
    "Company",
    "Location",
    "Found on",
    "First fetched",
    "Skip reason",
    "Link",
    KEY_COLUMN,
]


class SponsorStatusLookup:
    """exact / possible / not found, once per employer name per sync; blank
    when no register is loaded."""

    def __init__(self, repository: SponsorRepository):
        self._repository = repository
        self._register_loaded = repository.register_date() is not None
        self._by_name: dict[str, str] = {}

    def __call__(self, company_name: str) -> str:
        if not self._register_loaded:
            return ""
        if company_name not in self._by_name:
            matches = self._repository.find_sponsors(company_name)
            self._by_name[company_name] = (
                "exact"
                if any(match.is_exact for match in matches)
                else "possible"
                if matches
                else "not found"
            )
        return self._by_name[company_name]


# soonest deadline first, then the newest; no deadline last
def _jobs_order(listing: Listing) -> tuple[bool, str, float]:
    closes = listing.closes_at
    return (closes is None, closes.isoformat() if closes else "", -_first_fetched(listing).timestamp())


@dataclass(frozen=True)
class UserCells:
    # Status and Notes on the agent's rows, by listing key
    by_key: dict[str, list[str]]
    # whole rows the user added, as they were
    own_rows: list[list[str]]


def read_user_cells(existing_rows: list[list[str]]) -> UserCells:
    if not existing_rows or KEY_COLUMN not in existing_rows[0]:
        return UserCells({}, [])
    header = existing_rows[0]
    key_index = header.index(KEY_COLUMN)
    user_indexes = [header.index(column) for column in USER_COLUMNS if column in header]
    by_key: dict[str, list[str]] = {}
    own_rows: list[list[str]] = []
    for row in existing_rows[1:]:
        padded = row + [""] * (len(header) - len(row))
        if padded[key_index]:
            by_key[padded[key_index]] = [padded[index] for index in user_indexes]
        elif any(cell.strip() for cell in padded):
            own_rows.append(padded)
    return UserCells(by_key, own_rows)


def build_jobs_rows(
    listings: list[Listing], columns: list[SheetColumn], user_cells: UserCells
) -> list[list[str]]:
    header = [column.header for column in columns] + list(USER_COLUMNS) + [KEY_COLUMN]
    rows = [header]
    for listing in sorted(listings, key=_jobs_order):
        kept = user_cells.by_key.get(listing.key, [""] * len(USER_COLUMNS))
        rows.append([column.value(listing) for column in columns] + kept + [listing.key])
    # the user's own rows, after the agent's, cut or padded to the header
    rows += [(row + [""] * len(header))[: len(header)] for row in user_cells.own_rows]
    return rows


def build_skipped_rows(skipped: list[ScreenedListing]) -> list[list[str]]:
    ordered = sorted(
        skipped, key=lambda item: (item.skip_reason or "", -_first_fetched(item.listing).timestamp())
    )
    return [SKIPPED_HEADER] + [
        [
            item.listing.primary.title,
            item.listing.primary.company_name,
            item.listing.primary.location or "",
            ", ".join(item.listing.sources),
            _date(_first_fetched(item.listing)),
            item.skip_reason or "",
            item.listing.primary.url,
            item.listing.key,
        ]
        for item in ordered
    ]
