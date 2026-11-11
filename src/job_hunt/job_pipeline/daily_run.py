"""The daily run: every step that keeps the job list current, in order, each
on its own so one failing doesn't stop the rest."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from job_hunt.common.http_client import get_http_client
from job_hunt.common.logger import get_logger
from job_hunt.common.settings import settings as app_settings
from job_hunt.common.settings_schema import Settings
from job_hunt.company_discovery.board_search import BoardSearchDiscovery
from job_hunt.company_discovery.crawl_discovery import CrawlIndexDiscovery
from job_hunt.job_pipeline.job_fetch_run import JobFetchRun
from job_hunt.job_pipeline.source_health import HealthStatus, SourceHealthCheck
from job_hunt.job_sources.job_source_factory import JobSourceFactory
from job_hunt.job_store.sponsor_repository import SponsorRepository
from job_hunt.sheet_sync.google_sheet import GoogleSheetSync
from job_hunt.sponsor_register.register_update import update_sponsor_register

logger = get_logger(__name__)


@dataclass(frozen=True)
class StepOutcome:
    step: str
    succeeded: bool
    summary: str


class DailyRunStep(ABC):
    name: ClassVar[str]

    @abstractmethod
    def run(self) -> StepOutcome: ...


class SponsorRegisterStep(DailyRunStep):
    name = "sponsor register"

    def run(self) -> StepOutcome:
        update = update_sponsor_register(get_http_client(), SponsorRepository())
        if update.entries_loaded is None:
            return StepOutcome(self.name, True, f"register of {update.register_date} already loaded")
        return StepOutcome(
            self.name, True, f"loaded {update.entries_loaded} entries ({update.register_date})"
        )


class WebSearchDiscoveryStep(DailyRunStep):
    name = "web search discovery"

    def __init__(self, settings: Settings):
        self._settings = settings

    def run(self) -> StepOutcome:
        return StepOutcome(
            self.name, True, BoardSearchDiscovery.from_settings(self._settings).run().summary()
        )


class CommonCrawlDiscoveryStep(DailyRunStep):
    name = "Common Crawl discovery"

    def __init__(self, settings: Settings):
        self._settings = settings

    def run(self) -> StepOutcome:
        return StepOutcome(self.name, True, CrawlIndexDiscovery.from_settings(self._settings).run().summary())


class FetchJobsStep(DailyRunStep):
    name = "fetch"

    def __init__(self, settings: Settings):
        self._settings = settings

    def run(self) -> StepOutcome:
        reports = JobFetchRun(JobSourceFactory.create_sources(settings=self._settings)).run()
        failed = [report.source_name for report in reports if report.error]
        summary = (
            f"{len(reports)} sources, {sum(report.fetched_jobs for report in reports)} jobs "
            f"({sum(report.new_jobs for report in reports)} new), "
            f"{sum(report.companies_added for report in reports)} companies added"
        )
        if failed:
            summary += f"; failed: {', '.join(failed)}"
        return StepOutcome(self.name, not failed, summary)


class SourceHealthStep(DailyRunStep):
    name = "source health"

    def __init__(self, settings: Settings):
        self._settings = settings

    def run(self) -> StepOutcome:
        enabled = [
            source_name
            for source_name in JobSourceFactory.registered_source_names()
            if JobSourceFactory.is_enabled(source_name, self._settings)
        ]
        problems = [
            health
            for health in SourceHealthCheck(self._settings.source_health).check_all(only_sources=enabled)
            if health.status is not HealthStatus.OK
        ]
        if not problems:
            return StepOutcome(self.name, True, "every enabled source looks normal")
        summary = "; ".join(f"{health.source} {health.status.value}: {health.detail}" for health in problems)
        # a drop is worth a look but isn't a failure; a failing source is
        return StepOutcome(
            self.name, all(health.status is HealthStatus.WARNING for health in problems), summary
        )


class SheetSyncStep(DailyRunStep):
    name = "Google Sheet"

    def __init__(self, settings: Settings):
        self._settings = settings

    def run(self) -> StepOutcome:
        if not self._settings.google_sheet.spreadsheet_url:
            return StepOutcome(self.name, True, "skipped: no spreadsheet_url set")
        kept, skipped = GoogleSheetSync.from_settings(self._settings).sync()
        return StepOutcome(self.name, True, f"{kept} listings on Jobs, {skipped} on Skipped")


class DailyRun:
    def __init__(self, steps: list[DailyRunStep]):
        self._steps = steps

    @classmethod
    def from_settings(
        cls,
        settings: Settings = app_settings,
        include_discovery: bool = True,
        include_sponsor_register: bool = True,
    ) -> "DailyRun":
        steps: list[DailyRunStep] = []
        if include_sponsor_register:
            steps.append(SponsorRegisterStep())
        if include_discovery:
            steps += [WebSearchDiscoveryStep(settings), CommonCrawlDiscoveryStep(settings)]
        steps += [FetchJobsStep(settings), SourceHealthStep(settings)]
        if settings.google_sheet.enabled:
            steps.append(SheetSyncStep(settings))
        return cls(steps)

    @property
    def step_names(self) -> list[str]:
        return [step.name for step in self._steps]

    def run(self) -> list[StepOutcome]:
        outcomes = []
        for step in self._steps:
            logger.info("daily run: %s", step.name)
            try:
                outcome = step.run()
            # the run's outer boundary: a step that breaks is logged with its
            # traceback and reported, and the next step still runs
            except Exception as error:
                logger.exception("daily run: %s failed", step.name)
                outcome = StepOutcome(step.name, False, f"failed: {error or type(error).__name__}")
            outcomes.append(outcome)
        return outcomes
