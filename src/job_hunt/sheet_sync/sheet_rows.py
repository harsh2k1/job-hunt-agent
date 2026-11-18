"""What each tab of the job sheet shows: its columns in order, each column's
width, and which cells belong to the user.

Jobs: listings kept by the screening, best fit first. Status and Notes are
the user's and survive every sync; so do rows the user adds (no listing
key), which say "self" under Found by.
Skipped: everything screened out, with the reason, so nothing vanishes.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from job_hunt.job_pipeline.final_verdict import NOT_SCREENED, FinalVerdict
from job_hunt.job_pipeline.listings import Listing
from job_hunt.job_sources.job_source_factory import JobSourceFactory
from job_hunt.job_store.models import ListingScreening

KEY_COLUMN = "Listing key"
SEQUENCE_COLUMN = "#"
LINK_COLUMN = "Advert link"
CLOSES_COLUMN = "Closes"
# Google Sheets holds at most 50,000 characters in a cell
MAX_CELL_CHARACTERS = 45_000
FOUND_BY_AGENT = "agent"
# order within a tab: screened before not screened yet
VERDICT_ORDER = {"apply": 0, "check": 1, NOT_SCREENED: 2}
# Columns with fixed values: each value and its pastel colour (named in
# sheet_format.PASTELS). Such a column gets a dropdown and colour rules.
STATUS_CHOICES = {
    "To apply": "blue", "Applied": "purple", "Interviewing": "orange", "Offer": "green",
    "Rejected": "red", "Not interested": "grey",
}  # fmt: skip
KIND_CHOICES = {
    "graduate": "green", "internship": "blue", "part-time": "teal", "full-time": "orange",
    "freelance": "red", "studentship": "purple", "other": "grey",
}  # fmt: skip
FOUND_BY_CHOICES = {FOUND_BY_AGENT: "blue", "self": "purple"}
SPONSORSHIP_CHOICES = {"offered": "green", "not offered": "red", "not stated": "grey"}
# the sponsor register lookup, in words: whether the employer is on the
# Home Office register of licensed sponsors, by the same or a similar name
LICENCE_LABELS = {
    "exact": "On register (same name)",
    "possible": "On register? (similar name)",
    "not found": "Not on register",
}
LICENCE_CHOICES = dict(zip(LICENCE_LABELS.values(), ("green", "yellow", "red"), strict=True))
VERDICT_CHOICES = {"apply": "green", "check": "yellow", NOT_SCREENED: "grey"}


# every site's name; a plain dropdown, no colours
def found_on_choices() -> dict[str, str | None]:
    names = {JobSourceFactory.display_name(name) for name in JobSourceFactory.registered_source_names()}
    return dict.fromkeys(sorted(names, key=str.lower))


def _found_on(listing: Listing) -> str:
    return JobSourceFactory.display_name(listing.primary.source)


# the other sites listing the same advert
def _also_on(listing: Listing) -> str:
    shown = _found_on(listing)
    others = dict.fromkeys(JobSourceFactory.display_name(source) for source in listing.sources)
    return ", ".join(name for name in others if name != shown)


# a valid role's bullets, or what to check about one on To check
def _why(verdict: FinalVerdict) -> str:
    if verdict.valid_because:
        return verdict.valid_because
    return f"To check: {verdict.reason}" if verdict.reason else ""


ScreeningOf = Callable[[Listing], ListingScreening | None]
VerdictOf = Callable[[Listing], FinalVerdict]
CellValue = Callable[[Listing], str]


@dataclass(frozen=True)
class SheetColumn:
    header: str
    # width in pixels
    width: int
    # None: the user's column, never written by the agent
    value: CellValue | None = None
    centred: bool = False
    # shown in full over several lines (bullets); every other column is one
    # clipped line
    wraps: bool = False
    # fixed values -> colour name (None: no colour): a dropdown and colour coding
    choices: Mapping[str, str | None] | None = None
    # turns red while the role's deadline is within the next week
    marks_closing_soon: bool = False
    # stays in view while scrolling across; frozen columns come first
    frozen: bool = False

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


def _first_fetched_date(listing: Listing) -> str:
    return _date(_first_fetched(listing))


def jobs_columns(
    sponsor_status: Callable[[str], str], screening_of: ScreeningOf, verdict_of: VerdictOf
) -> list[SheetColumn]:
    def screened(read: Callable[[ListingScreening], str | int | None]) -> CellValue:
        def value(listing: Listing) -> str:
            screening = screening_of(listing)
            result = read(screening) if screening else None
            return "" if result is None else str(result)

        return value

    return [
        # kept in view while scrolling across: which row, since when, what, the user's status
        SheetColumn(SEQUENCE_COLUMN, 45, centred=True, marks_closing_soon=True, frozen=True),
        SheetColumn("First fetched", 105, _first_fetched_date, centred=True, frozen=True),
        SheetColumn("Role", 300, lambda listing: listing.primary.title, marks_closing_soon=True, frozen=True),
        SheetColumn("Company", 180, lambda listing: listing.primary.company_name, frozen=True),
        SheetColumn("Status", 120, centred=True, choices=STATUS_CHOICES, frozen=True),
        # where to read it, what kind it is, where it was found and its dates
        SheetColumn(LINK_COLUMN, 260, lambda listing: listing.primary.url),
        SheetColumn(
            "Kind", 100, screened(lambda screening: screening.kind), centred=True, choices=KIND_CHOICES
        ),
        SheetColumn("Found on", 160, _found_on, centred=True, choices=found_on_choices()),
        SheetColumn("Posted", 95, lambda listing: _date(listing.primary.posted_at), centred=True),
        SheetColumn(
            CLOSES_COLUMN, 95, lambda listing: _date(listing.closes_at), centred=True, marks_closing_soon=True
        ),
        # why it's on the sheet, and the advert
        SheetColumn("Why it's valid", 330, lambda listing: _why(verdict_of(listing)), wraps=True),
        SheetColumn("JD", 260, lambda listing: (listing.primary.description or "")[:MAX_CELL_CHARACTERS]),
        # further facts
        SheetColumn("Location", 160, lambda listing: listing.primary.location or ""),
        SheetColumn("Type", 150, lambda listing: listing.primary.employment_type or ""),
        SheetColumn("Also on", 170, _also_on),
        SheetColumn("Found by", 80, lambda listing: FOUND_BY_AGENT, centred=True, choices=FOUND_BY_CHOICES),
        SheetColumn(
            "Sponsorship in advert",
            140,
            screened(lambda screening: screening.sponsorship),
            centred=True,
            choices=SPONSORSHIP_CHOICES,
        ),
        SheetColumn(
            "Sponsor licence",
            190,
            lambda listing: LICENCE_LABELS.get(sponsor_status(listing.primary.company_name), ""),
            centred=True,
            choices=LICENCE_CHOICES,
        ),
        SheetColumn("Start", 120, screened(lambda screening: screening.start_date)),
        SheetColumn("Salary", 150, lambda listing: listing.primary.salary_text or ""),
        # the screening's verdict, then the user's notes
        SheetColumn(
            "Verdict", 110, lambda listing: verdict_of(listing).verdict, centred=True, choices=VERDICT_CHOICES
        ),
        SheetColumn("Notes", 220),
        SheetColumn(KEY_COLUMN, 60),
    ]


def skipped_columns(reason_of: Callable[[Listing], str]) -> list[SheetColumn]:
    return [
        SheetColumn(SEQUENCE_COLUMN, 45, centred=True, frozen=True),
        SheetColumn("First fetched", 105, _first_fetched_date, centred=True, frozen=True),
        SheetColumn("Role", 300, lambda listing: listing.primary.title, frozen=True),
        SheetColumn("Company", 180, lambda listing: listing.primary.company_name, frozen=True),
        SheetColumn(LINK_COLUMN, 260, lambda listing: listing.primary.url),
        SheetColumn("Location", 160, lambda listing: listing.primary.location or ""),
        SheetColumn("Found on", 160, _found_on, centred=True, choices=found_on_choices()),
        SheetColumn("Also on", 170, _also_on),
        SheetColumn("Skip reason", 340, reason_of),
        SheetColumn(KEY_COLUMN, 60),
    ]


@dataclass(frozen=True)
class UserCells:
    # the user's cells on the agent's rows: listing key -> header -> value
    by_key: dict[str, dict[str, str]]
    # rows the user added, header -> value, so they survive a layout change
    own_rows: list[dict[str, str]]


def merge_user_cells(*tabs: UserCells) -> UserCells:
    by_key: dict[str, dict[str, str]] = {}
    for tab in tabs:
        by_key.update(tab.by_key)
    # own rows live on the first tab (Jobs) only
    return UserCells(by_key, tabs[0].own_rows if tabs else [])


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


# ready to apply first; then soonest deadline, no deadline last; then newest
def _jobs_order(listing: Listing, verdict_of: VerdictOf) -> tuple[int, bool, str, float]:
    closes = listing.closes_at
    return (
        VERDICT_ORDER.get(verdict_of(listing).verdict, len(VERDICT_ORDER)),
        closes is None,
        closes.isoformat() if closes else "",
        -_first_fetched(listing).timestamp(),
    )


def build_rows(columns: list[SheetColumn], listings: list[Listing], user_cells: UserCells) -> list[list[str]]:
    rows = [[column.header for column in columns]]
    for listing in listings:
        kept = next(
            (
                user_cells.by_key[key]
                for key in (listing.key, *listing.merged_keys)
                if key in user_cells.by_key
            ),
            {},
        )
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
    listings: list[Listing], columns: list[SheetColumn], user_cells: UserCells, verdict_of: VerdictOf
) -> list[list[str]]:
    ordered = sorted(listings, key=lambda listing: _jobs_order(listing, verdict_of))
    return build_rows(columns, ordered, user_cells)


# grouped by reason, newest first within each
def build_skipped_rows(skipped: list[ScreenedListing]) -> list[list[str]]:
    reasons = {item.listing.key: item.skip_reason or "" for item in skipped}
    ordered = sorted(
        skipped, key=lambda item: (item.skip_reason or "", -_first_fetched(item.listing).timestamp())
    )
    columns = skipped_columns(lambda listing: reasons[listing.key])
    return build_rows(columns, [item.listing for item in ordered], UserCells({}, []))
