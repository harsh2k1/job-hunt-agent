import json

import gspread

from job_hunt.common.logger import get_logger
from job_hunt.common.settings import settings as app_settings
from job_hunt.common.settings_schema import Settings
from job_hunt.job_pipeline.listings import Listing
from job_hunt.job_pipeline.live_listings import rule_screened_live_listings
from job_hunt.job_store.job_repository import JobRepository
from job_hunt.job_store.models import ListingScreening, utc_now
from job_hunt.job_store.screening_repository import ScreeningRepository
from job_hunt.job_store.sponsor_repository import SponsorRepository
from job_hunt.sheet_sync.sheet_format import format_tab
from job_hunt.sheet_sync.sheet_rows import (
    ScreenedListing,
    SheetColumn,
    SponsorStatusLookup,
    build_jobs_rows,
    build_skipped_rows,
    fit_of,
    jobs_columns,
    read_user_cells,
    skipped_columns,
)

logger = get_logger(__name__)


class SheetSyncError(RuntimeError):
    pass


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

    def sync(self) -> tuple[int, int]:
        spreadsheet = self._open_spreadsheet()
        live = rule_screened_live_listings(self._settings, self._job_repository, utc_now())
        screenings = self._screening_repository.find(listing.key for listing in live.kept)

        def screening_of(listing: Listing) -> ListingScreening | None:
            return screenings.get(listing.key)

        llm_skipped = [listing for listing in live.kept if fit_of(screening_of(listing)) == "skip"]
        kept = [listing for listing in live.kept if fit_of(screening_of(listing)) != "skip"]
        skipped = [ScreenedListing(listing, reason) for listing, reason in live.skipped] + [
            ScreenedListing(listing, f"LLM: {screenings[listing.key].reason}") for listing in llm_skipped
        ]

        jobs_tab = self._worksheet(spreadsheet, self._sheet_settings.jobs_worksheet)
        columns = jobs_columns(SponsorStatusLookup(self._sponsor_repository), screening_of)
        user_cells = read_user_cells(jobs_tab.get_all_values())
        self._rewrite(jobs_tab, build_jobs_rows(kept, columns, user_cells, screening_of), columns)
        skipped_tab = self._worksheet(spreadsheet, self._sheet_settings.skipped_worksheet)
        self._rewrite(skipped_tab, build_skipped_rows(skipped), skipped_columns(lambda listing: ""))
        logger.info("sheet: %d listings on Jobs, %d on Skipped", len(kept), len(skipped))
        return len(kept), len(skipped)

    @staticmethod
    def _rewrite(worksheet: gspread.Worksheet, rows: list[list[str]], columns: list[SheetColumn]) -> None:
        worksheet.clear()
        worksheet.resize(rows=max(len(rows), 2), cols=len(rows[0]))
        worksheet.update(rows, "A1", value_input_option="RAW")
        format_tab(worksheet, columns, rows)

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
