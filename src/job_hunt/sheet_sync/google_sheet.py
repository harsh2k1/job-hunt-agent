import json
from dataclasses import dataclass

import gspread

from job_hunt.common.logger import get_logger
from job_hunt.common.settings import settings as app_settings
from job_hunt.common.settings_schema import Settings
from job_hunt.job_pipeline.final_verdict import NOT_SCREENED, UNRELATED, FinalVerdict, final_verdict
from job_hunt.job_pipeline.listings import Listing
from job_hunt.job_pipeline.live_listings import live_listings
from job_hunt.job_pipeline.sponsor_status import SponsorStatusLookup
from job_hunt.job_store.job_repository import JobRepository
from job_hunt.job_store.models import ListingScreening, utc_now
from job_hunt.job_store.screening_repository import ScreeningRepository
from job_hunt.job_store.sponsor_repository import SponsorRepository
from job_hunt.job_store.title_screening_repository import TitleScreeningRepository
from job_hunt.sheet_sync.sheet_check import check_choices, check_tab, check_tab_order, check_verdicts
from job_hunt.sheet_sync.sheet_format import format_tab
from job_hunt.sheet_sync.sheet_rows import (
    ScreenedListing,
    SheetColumn,
    UserCells,
    build_jobs_rows,
    build_skipped_rows,
    jobs_columns,
    merge_user_cells,
    read_user_cells,
    skipped_columns,
)

logger = get_logger(__name__)


class SheetSyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class SheetCounts:
    apply: int
    check: int
    skipped: int
    # unrelated or closed: remembered, not shown
    hidden: int
    # not screened yet: shown once they are (a later run), so To check only
    # holds roles the screening found could be the candidate's
    waiting: int = 0

    def __str__(self) -> str:
        return (
            f"{self.apply} to apply on Jobs, {self.check} on To check, {self.skipped} on Skipped "
            f"(related but not open to you); {self.hidden} unrelated or closed, not shown; "
            f"{self.waiting} waiting to be screened"
        )


class GoogleSheetSync:
    """Rewrites the Jobs and Skipped tabs from the current listings, keeping
    the user's Status, Notes and own rows. Values are written as plain text
    (RAW), so nothing in an advert can become a formula."""

    def __init__(
        self,
        settings: Settings,
        job_repository: JobRepository,
        sponsor_repository: SponsorRepository,
        screening_repository: ScreeningRepository,
    ):
        self._settings = settings
        self._sheet_settings = settings.google_sheet
        self._job_repository = job_repository
        self._sponsor_repository = sponsor_repository
        self._screening_repository = screening_repository

    @classmethod
    def from_settings(cls, settings: Settings = app_settings) -> "GoogleSheetSync":
        return cls(settings, JobRepository(), SponsorRepository(), ScreeningRepository())

    # Jobs: only roles the candidate can apply to now ("apply"). To check:
    # what the advert doesn't settle, never mixed into Jobs. Skipped:
    # related roles the candidate can't take, with the reason. A listing not
    # screened yet waits off the sheet until a run screens it.
    def sync(self) -> SheetCounts:
        spreadsheet = self._open_spreadsheet()
        live = live_listings(self._settings, self._job_repository, TitleScreeningRepository(), utc_now())
        screenings = self._screening_repository.find(listing.key for listing in live.kept)

        def screening_of(listing: Listing) -> ListingScreening | None:
            return screenings.get(listing.key)

        licence = SponsorStatusLookup(self._sponsor_repository)
        visa_ends = self._settings.llm_screening.graduate_visa_ends
        verdicts = {
            listing.key: final_verdict(
                screening_of(listing), licence(listing.primary.company_name), visa_ends
            )
            for listing in live.kept
        }

        def verdict_of(listing: Listing) -> FinalVerdict:
            return verdicts[listing.key]

        by_verdict: dict[str, list[Listing]] = {
            "apply": [], "check": [], "skip": [], UNRELATED: [], NOT_SCREENED: [],
        }  # fmt: skip
        for listing in live.kept:
            by_verdict[verdict_of(listing).verdict].append(listing)
        # Skipped holds only related roles the candidate can't take, so wrong
        # skips can be spotted; unrelated and closed listings aren't shown
        skipped = [ScreenedListing(listing, verdict_of(listing).reason) for listing in by_verdict["skip"]]

        jobs_tab = self._worksheet(spreadsheet, self._sheet_settings.jobs_worksheet)
        checks_tab = self._worksheet(spreadsheet, self._sheet_settings.checks_worksheet)
        skipped_tab = self._worksheet(spreadsheet, self._sheet_settings.skipped_worksheet)
        columns = jobs_columns(licence, screening_of, verdict_of)
        # the user's Status and Notes follow a listing between Jobs and To check
        user_cells = merge_user_cells(
            read_user_cells(jobs_tab.get_all_values()), read_user_cells(checks_tab.get_all_values())
        )
        jobs = build_jobs_rows(by_verdict["apply"], columns, user_cells, verdict_of)
        checks = build_jobs_rows(by_verdict["check"], columns, UserCells(user_cells.by_key, []), verdict_of)
        skipped_columns_used = skipped_columns(lambda listing: "")
        skipped_rows = build_skipped_rows(skipped)
        # a value outside a column's dropdown is a defect: nothing is written
        stray = (
            check_choices(jobs_tab.title, jobs, columns)
            + check_choices(checks_tab.title, checks, columns)
            + check_choices(skipped_tab.title, skipped_rows, skipped_columns_used)
        )
        if stray:
            raise SheetSyncError("; ".join(stray))
        self._rewrite(jobs_tab, jobs, columns)
        self._rewrite(checks_tab, checks, columns)
        self._rewrite(skipped_tab, skipped_rows, skipped_columns_used)
        # Jobs, then To check, then Skipped, ahead of any tab of the user's
        ours = [jobs_tab, checks_tab, skipped_tab]
        tab_titles = [tab.title for tab in ours]
        spreadsheet.reorder_worksheets(
            ours + [tab for tab in spreadsheet.worksheets() if tab.title not in tab_titles]
        )
        # read back what Google now shows, and fail loudly if it isn't right
        problems = (
            check_tab(jobs_tab, columns)
            + check_tab(checks_tab, columns)
            + check_tab(skipped_tab, skipped_columns_used)
            + check_verdicts(jobs_tab, {"apply"})
            + check_verdicts(checks_tab, {"check"})
            + check_tab_order(spreadsheet, tab_titles)
        )
        if problems:
            raise SheetSyncError("the sheet doesn't look as it should: " + "; ".join(problems[:10]))
        counts = SheetCounts(
            len(by_verdict["apply"]),
            len(by_verdict["check"]),
            len(skipped),
            hidden=len(live.skipped) + len(by_verdict[UNRELATED]),
            waiting=len(by_verdict[NOT_SCREENED]),
        )
        logger.info("sheet: %s", counts)
        return counts

    @staticmethod
    def _rewrite(worksheet: gspread.Worksheet, rows: list[list[str]], columns: list[SheetColumn]) -> None:
        worksheet.clear()
        worksheet.resize(rows=max(len(rows), 2), cols=len(rows[0]))
        worksheet.update(rows, "A1", value_input_option="RAW")
        format_tab(worksheet, columns, rows)
        # Everything goes in as plain text (RAW), so nothing in an advert can
        # become a formula; a date column is the user's own, written again
        # so its values are dates, not text.
        for index, column in enumerate(columns):
            if column.is_date and len(rows) > 1:
                letter = gspread.utils.rowcol_to_a1(1, index + 1).rstrip("1")
                values = [[row[index] if index < len(row) else ""] for row in rows[1:]]
                worksheet.update(values, f"{letter}2", value_input_option="USER_ENTERED")

    def _open_spreadsheet(self) -> gspread.Spreadsheet:
        if self._sheet_settings.service_account is None or not self._sheet_settings.spreadsheet_url:
            raise SheetSyncError(
                "set google_sheet.spreadsheet_url (settings.local.yaml) and the service account key"
            )
        client = gspread.service_account_from_dict(
            json.loads(self._sheet_settings.service_account.get_secret_value())
        )
        try:
            return client.open_by_url(self._sheet_settings.spreadsheet_url)
        except (gspread.SpreadsheetNotFound, gspread.exceptions.APIError) as error:
            raise SheetSyncError(
                f"can't open the sheet (is it shared with the service account?): {error}"
            ) from error

    @staticmethod
    def _worksheet(spreadsheet: gspread.Spreadsheet, title: str) -> gspread.Worksheet:
        try:
            return spreadsheet.worksheet(title)
        except gspread.WorksheetNotFound:
            return spreadsheet.add_worksheet(title, rows=100, cols=20)
