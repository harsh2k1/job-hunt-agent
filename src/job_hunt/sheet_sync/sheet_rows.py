"""What each tab of the job sheet shows: its columns in order, each column's
width, and which cells belong to the user.

Jobs: listings kept by the screening, best fit first. Status and Notes are
the user's and survive every sync; so do rows the user adds (no listing
key), which say "self" under Found by.
Skipped: everything screened out, with the reason, so nothing vanishes.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from job_hunt.job_pipeline.listings import Listing
from job_hunt.job_store.models import ListingScreening
from job_hunt.job_store.sponsor_repository import SponsorRepository

KEY_COLUMN = "Listing key"
SEQUENCE_COLUMN = "#"
LINK_COLUMN = "Advert link"
# Google Sheets holds at most 50,000 characters in a cell
MAX_CELL_CHARACTERS = 45_000
FOUND_BY_AGENT = "agent"
NOT_SCREENED = "not screened yet"
# Jobs tab order: the best fits first
FIT_ORDER = {"strong": 0, "good": 1, "check": 2, NOT_SCREENED: 3}
STATUS_CHOICES = ("To apply", "Applied", "Interviewing", "Offer", "Rejected", "Not interested")

ScreeningOf = Callable[[Listing], ListingScreening | None]
CellValue = Callable[[Listing], str]


@dataclass(frozen=True)
class SheetColumn:
    header: str
    # width in pixels
    width: int
    # None: the user's column, never written by the agent
    value: CellValue | None = None
    centred: bool = False

    @property
    def belongs_to_user(self) -> bool:
        return self.value is None and self.header not in (SEQUENCE_COLUMN, KEY_COLUMN)


@dataclass(frozen=True)
class ScreenedListing:
    listing: Listing
    # None when kept
    skip_reason: str | None


def _date(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d") if value else ""


def _first_fetched(listing: Listing) -> datetime:
    return min(job.first_seen_at for job in listing.sightings)


def fit_of(screening: ListingScreening | None) -> str:
    return screening.fit if screening else NOT_SCREENED


def jobs_columns(sponsor_status: Callable[[str], str], screening_of: ScreeningOf) -> list[SheetColumn]:
    def screened(read: Callable[[ListingScreening], str | int | None]) -> CellValue:
        def value(listing: Listing) -> str:
            screening = screening_of(listing)
            result = read(screening) if screening else None
            return "" if result is None else str(result)

        return value

    return [
        # the role: what it is, where to read it, where it was found
        SheetColumn(SEQUENCE_COLUMN, 45, centred=True),
        SheetColumn("Role", 300, lambda listing: listing.primary.title),
        SheetColumn("Company", 180, lambda listing: listing.primary.company_name),
        SheetColumn(LINK_COLUMN, 120, lambda listing: listing.primary.url, centred=True),
        SheetColumn("JD", 260, lambda listing: (listing.primary.description or "")[:MAX_CELL_CHARACTERS]),
        SheetColumn("Location", 160, lambda listing: listing.primary.location or ""),
        SheetColumn("Type", 150, lambda listing: listing.primary.employment_type or ""),
        SheetColumn("Found on", 170, lambda listing: ", ".join(listing.sources)),
        SheetColumn("Found by", 80, lambda listing: FOUND_BY_AGENT, centred=True),
        # facts: visa and dates
        SheetColumn("Sponsorship in advert", 140, screened(lambda screening: screening.sponsorship), True),
        SheetColumn(
            "Sponsor licence", 110, lambda listing: sponsor_status(listing.primary.company_name), True
        ),
        SheetColumn("Closes", 95, lambda listing: _date(listing.closes_at), centred=True),
        SheetColumn("Start", 120, screened(lambda screening: screening.start_date)),
        SheetColumn("Posted", 95, lambda listing: _date(listing.primary.posted_at), centred=True),
        SheetColumn("First fetched", 105, lambda listing: _date(_first_fetched(listing)), centred=True),
        # derived: pay, the screening's verdict, then the user's columns
        SheetColumn("Salary", 150, lambda listing: listing.primary.salary_text or ""),
        SheetColumn("Fit", 90, lambda listing: fit_of(screening_of(listing)), centred=True),
        SheetColumn("Why", 320, screened(lambda screening: screening.reason)),
        SheetColumn("Status", 120, centred=True),
        SheetColumn("Notes", 220),
        SheetColumn(KEY_COLUMN, 60),
    ]


def skipped_columns(reason_of: Callable[[Listing], str]) -> list[SheetColumn]:
    return [
        SheetColumn(SEQUENCE_COLUMN, 45, centred=True),
        SheetColumn("Role", 300, lambda listing: listing.primary.title),
        SheetColumn("Company", 180, lambda listing: listing.primary.company_name),
        SheetColumn(LINK_COLUMN, 120, lambda listing: listing.primary.url, centred=True),
        SheetColumn("Location", 160, lambda listing: listing.primary.location or ""),
        SheetColumn("Found on", 170, lambda listing: ", ".join(listing.sources)),
        SheetColumn("First fetched", 105, lambda listing: _date(_first_fetched(listing)), centred=True),
        SheetColumn("Skip reason", 340, reason_of),
        SheetColumn(KEY_COLUMN, 60),
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


@dataclass(frozen=True)
class UserCells:
    # the user's cells on the agent's rows: listing key -> header -> value
    by_key: dict[str, dict[str, str]]
    # rows the user added, header -> value, so they survive a layout change
    own_rows: list[dict[str, str]]


def read_user_cells(existing_rows: list[list[str]]) -> UserCells:
    if not existing_rows or KEY_COLUMN not in existing_rows[0]:
        return UserCells({}, [])
    header = existing_rows[0]
    by_key: dict[str, dict[str, str]] = {}
    own_rows: list[dict[str, str]] = []
    for row in existing_rows[1:]:
        cells = dict(zip(header, row + [""] * (len(header) - len(row)), strict=True))
        if cells[KEY_COLUMN]:
            by_key[cells[KEY_COLUMN]] = cells
        elif any(value.strip() for name, value in cells.items() if name != SEQUENCE_COLUMN):
            own_rows.append(cells)
    return UserCells(by_key, own_rows)


# best fit first; then soonest deadline, no deadline last; then newest
def _jobs_order(listing: Listing, screening_of: ScreeningOf) -> tuple[int, bool, str, float]:
    closes = listing.closes_at
    return (
        FIT_ORDER.get(fit_of(screening_of(listing)), len(FIT_ORDER)),
        closes is None,
        closes.isoformat() if closes else "",
        -_first_fetched(listing).timestamp(),
    )


def build_rows(columns: list[SheetColumn], listings: list[Listing], user_cells: UserCells) -> list[list[str]]:
    rows = [[column.header for column in columns]]
    for listing in listings:
        kept = user_cells.by_key.get(listing.key, {})
        rows.append([_cell(column, listing, kept, len(rows)) for column in columns])
    # the user's own rows, after the agent's, each value under its own header
    for own_row in user_cells.own_rows:
        rows.append(
            [
                str(len(rows)) if column.header == SEQUENCE_COLUMN else own_row.get(column.header, "")
                for column in columns
            ]
        )
    return rows


def _cell(column: SheetColumn, listing: Listing, user_cells: dict[str, str], position: int) -> str:
    if column.header == SEQUENCE_COLUMN:
        return str(position)
    if column.header == KEY_COLUMN:
        return listing.key
    if column.value is None:
        return user_cells.get(column.header, "")
    return column.value(listing)


def build_jobs_rows(
    listings: list[Listing], columns: list[SheetColumn], user_cells: UserCells, screening_of: ScreeningOf
) -> list[list[str]]:
    ordered = sorted(listings, key=lambda listing: _jobs_order(listing, screening_of))
    return build_rows(columns, ordered, user_cells)


# grouped by reason, newest first within each
def build_skipped_rows(skipped: list[ScreenedListing]) -> list[list[str]]:
    reasons = {item.listing.key: item.skip_reason or "" for item in skipped}
    ordered = sorted(
        skipped, key=lambda item: (item.skip_reason or "", -_first_fetched(item.listing).timestamp())
    )
    columns = skipped_columns(lambda listing: reasons[listing.key])
    return build_rows(columns, [item.listing for item in ordered], UserCells({}, []))
