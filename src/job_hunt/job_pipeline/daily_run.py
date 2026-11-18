"""The daily run: every step that keeps the job list current, in order, each
on its own so one failing doesn't stop the rest. Each step runs every so
many days (daily_run.every_days): a step that isn't due says so and is
skipped, and a step's day is recorded only when it succeeds."""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import ClassVar

from job_hunt.common.http_client import get_http_client
from job_hunt.common.logger import get_logger
from job_hunt.common.settings import settings as app_settings
from job_hunt.common.settings_schema import Settings
from job_hunt.company_discovery.board_search import BoardSearchDiscovery
from job_hunt.company_discovery.crawl_discovery import CrawlIndexDiscovery
from job_hunt.job_pipeline.job_fetch_run import JobFetchRun
from job_hunt.job_pipeline.screening_run import screen_live_listings
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
    # not due today: neither run nor failed
    skipped: bool = False


class DailyRunStep(ABC):
    name: ClassVar[str]
    # its line in daily_run.every_days
    key: ClassVar[str]

    @abstractmethod
    def run(self) -> StepOutcome: ...


class SponsorRegisterStep(DailyRunStep):
    name = "sponsor register"
    key = "sponsor_register"

    def run(self) -> StepOutcome:
        update = update_sponsor_register(get_http_client(), SponsorRepository())
        if update.entries_loaded is None:
            return StepOutcome(self.name, True, f"register of {update.register_date} already loaded")
        return StepOutcome(
            self.name, True, f"loaded {update.entries_loaded} entries ({update.register_date})"
        )


class WebSearchDiscoveryStep(DailyRunStep):
    name = "web search discovery"
    key = "web_search_discovery"

    def __init__(self, settings: Settings):
        self._settings = settings

    def run(self) -> StepOutcome:
        return StepOutcome(
            self.name, True, BoardSearchDiscovery.from_settings(self._settings).run().summary()
        )


class CommonCrawlDiscoveryStep(DailyRunStep):
    name = "Common Crawl discovery"
    key = "common_crawl_discovery"

    def __init__(self, settings: Settings):
        self._settings = settings

    def run(self) -> StepOutcome:
        return StepOutcome(self.name, True, CrawlIndexDiscovery.from_settings(self._settings).run().summary())


class FetchJobsStep(DailyRunStep):
    name = "fetch"
    key = "fetch"

    def __init__(self, settings: Settings):
        self._settings = settings

    def run(self) -> StepOutcome:
        reports = JobFetchRun(
            JobSourceFactory.create_sources(settings=self._settings),
            max_parallel=self._settings.daily_run.max_parallel_sources,
        ).run()
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
    key = "source_health"

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


class LlmScreeningStep(DailyRunStep):
    name = "LLM screening"
    key = "llm_screening"

    def __init__(self, settings: Settings):
        self._settings = settings

    def run(self) -> StepOutcome:
        report = screen_live_listings(self._settings)
        return StepOutcome(self.name, report.succeeded, report.summary())


class SheetSyncStep(DailyRunStep):
    name = "Google Sheet"
    key = "google_sheet"

    def __init__(self, settings: Settings):
        self._settings = settings

    def run(self) -> StepOutcome:
        if not self._settings.google_sheet.spreadsheet_url:
            return StepOutcome(self.name, True, "skipped: no spreadsheet_url set")
        counts = GoogleSheetSync.from_settings(self._settings).sync()
        return StepOutcome(self.name, True, str(counts))


class StepRunLog:
    """The day each step last succeeded, in a small JSON file."""

    def __init__(self, path: Path):
        self._path = path

    def last_run(self, key: str) -> date | None:
        text = self._read().get(key)
        return date.fromisoformat(text) if text else None

    def record(self, key: str, day: date) -> None:
        days = self._read()
        days[key] = day.isoformat()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(days, indent=2, sort_keys=True))

    def _read(self) -> dict[str, str]:
        return json.loads(self._path.read_text()) if self._path.exists() else {}


class DailyRun:
    def __init__(
        self,
        steps: list[DailyRunStep],
        every_days: dict[str, int] | None = None,
        run_log: StepRunLog | None = None,
        today: date | None = None,
    ):
        self._steps = steps
        # None: every step runs (by hand: --all-steps)
        self._every_days = every_days
        self._run_log = run_log
        self._today = today or date.today()

    @classmethod
    def from_settings(
        cls,
        settings: Settings = app_settings,
        include_discovery: bool = True,
        include_sponsor_register: bool = True,
        all_steps: bool = False,
    ) -> "DailyRun":
        steps: list[DailyRunStep] = []
        if include_sponsor_register:
            steps.append(SponsorRegisterStep())
        if include_discovery:
            steps += [WebSearchDiscoveryStep(settings), CommonCrawlDiscoveryStep(settings)]
        steps += [FetchJobsStep(settings), SourceHealthStep(settings)]
        if settings.llm_screening.enabled:
            steps.append(LlmScreeningStep(settings))
        if settings.google_sheet.enabled:
            steps.append(SheetSyncStep(settings))
        every_days = None if all_steps else settings.daily_run.every_days.model_dump()
        return cls(steps, every_days, StepRunLog(settings.daily_run.state_file))

    @property
    def step_names(self) -> list[str]:
        return [step.name for step in self._steps]

    def run(self) -> list[StepOutcome]:
        outcomes = []
        for step in self._steps:
            not_due = self._not_due(step)
            if not_due:
                logger.info("daily run: %s %s", step.name, not_due)
                outcomes.append(StepOutcome(step.name, True, not_due, skipped=True))
                continue
            logger.info("daily run: %s", step.name)
            try:
                outcome = step.run()
            # the run's outer boundary: a step that breaks is logged with its
            # traceback and reported, and the next step still runs
            except Exception as error:
                logger.exception("daily run: %s failed", step.name)
                outcome = StepOutcome(step.name, False, f"failed: {error or type(error).__name__}")
            if outcome.succeeded and self._run_log is not None:
                self._run_log.record(step.key, self._today)
            outcomes.append(outcome)
        return outcomes

    # why a step isn't due today, or "" when it is
    def _not_due(self, step: DailyRunStep) -> str:
        if self._every_days is None or self._run_log is None:
            return ""
        every = self._every_days[step.key]
        last = self._run_log.last_run(step.key)
        if last is None or (self._today - last).days >= every:
            return ""
        return f"not due: every {every} days, last run {last.isoformat()}"
