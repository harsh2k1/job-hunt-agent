import json
from datetime import timedelta

import gspread

from job_hunt.common.logger import get_logger
from job_hunt.common.settings import settings as app_settings
from job_hunt.common.settings_schema import GoogleSheetSettings, Settings
from job_hunt.job_pipeline.listings import ListingBuilder
from job_hunt.job_pipeline.rule_screening import RuleScreening
from job_hunt.job_sources.location_matching import TargetLocationMatcher
from job_hunt.job_store.job_repository import JobRepository
from job_hunt.job_store.models import utc_now
from job_hunt.job_store.sponsor_repository import SponsorRepository
from job_hunt.sheet_sync.sheet_rows import (
    ScreenedListing,
    SponsorStatusLookup,
    build_jobs_rows,
    build_skipped_rows,
    jobs_columns,
    read_user_cells,
)

logger = get_logger(__name__)


class SheetSyncError(RuntimeError):
    pass


class GoogleSheetSync:
    """Rewrites the Jobs and Skipped tabs from the current listings,
    keeping the user's Status, Notes and own rows. Values are written as
    plain text (RAW), so nothing in an advert can become a formula."""

    def __init__(
        self,
        sheet_settings: GoogleSheetSettings,
        listing_builder: ListingBuilder,
        screening: RuleScreening,
        job_repository: JobRepository,
        sponsor_repository: SponsorRepository,
    ):
        self._settings = sheet_settings
        self._listing_builder = listing_builder
        self._screening = screening
        self._job_repository = job_repository
        self._sponsor_repository = sponsor_repository

    @classmethod
    def from_settings(cls, settings: Settings = app_settings) -> "GoogleSheetSync":
        return cls(
            settings.google_sheet,
            ListingBuilder(TargetLocationMatcher(settings.job_search.target_location_keywords)),
            RuleScreening(settings.screening),
            JobRepository(),
            SponsorRepository(),
        )

    def sync(self) -> tuple[int, int]:
        spreadsheet = self._open_spreadsheet()
        now = utc_now()
        listings = self._listing_builder.build(
            self._job_repository.list_jobs_seen_since(now - timedelta(days=self._settings.listing_days))
        )
        screened = [
            ScreenedListing(listing, self._screening.screen(listing, now).reason) for listing in listings
        ]
        kept = [item.listing for item in screened if item.skip_reason is None]
        skipped = [item for item in screened if item.skip_reason is not None]

        jobs_tab = self._worksheet(spreadsheet, self._settings.jobs_worksheet)
        columns = jobs_columns(SponsorStatusLookup(self._sponsor_repository))
        self._rewrite(jobs_tab, build_jobs_rows(kept, columns, read_user_cells(jobs_tab.get_all_values())))
        self._rewrite(
            self._worksheet(spreadsheet, self._settings.skipped_worksheet), build_skipped_rows(skipped)
        )
        logger.info("sheet: %d listings on Jobs, %d on Skipped", len(kept), len(skipped))
        return len(kept), len(skipped)

    @staticmethod
    def _rewrite(worksheet: gspread.Worksheet, rows: list[list[str]]) -> None:
        worksheet.clear()
        worksheet.resize(rows=max(len(rows), 2), cols=len(rows[0]))
        worksheet.update(rows, "A1", value_input_option="RAW")
        worksheet.freeze(rows=1)
        worksheet.format("1:1", {"textFormat": {"bold": True}})
        worksheet.set_basic_filter()

    def _open_spreadsheet(self) -> gspread.Spreadsheet:
        if self._settings.service_account is None or not self._settings.spreadsheet_url:
            raise SheetSyncError(
                "set google_sheet.spreadsheet_url (settings.local.yaml) and the service account key"
            )
        client = gspread.service_account_from_dict(
            json.loads(self._settings.service_account.get_secret_value())
        )
        try:
            return client.open_by_url(self._settings.spreadsheet_url)
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
