import statistics
from dataclasses import dataclass
from enum import Enum

from job_hunt.common.settings_schema import SourceHealthSettings
from job_hunt.job_store.source_run_repository import SourceRunRepository


class HealthStatus(str, Enum):
    OK = "ok"
    # still running, but returning far fewer jobs than it usually does: a
    # site changed its layout, blocked us, or an API key ran out
    WARNING = "warning"
    FAILING = "failing"


@dataclass(frozen=True)
class SourceHealth:
    source: str
    status: HealthStatus
    detail: str


class SourceHealthCheck:
    """Judges each source by its own history, since normal counts range from
    a handful (HN) to thousands (company boards)."""

    def __init__(
        self, health_settings: SourceHealthSettings, run_repository: SourceRunRepository | None = None
    ):
        self._settings = health_settings
        self._run_repository = run_repository or SourceRunRepository()

    # only_sources: e.g. the enabled ones, so a source switched off long ago
    # doesn't show as failing for ever
    def check_all(self, only_sources: list[str] | None = None) -> list[SourceHealth]:
        sources = self._run_repository.sources_with_runs()
        if only_sources is not None:
            sources = [source for source in sources if source in only_sources]
        return [self.check(source) for source in sources]

    def check(self, source: str) -> SourceHealth:
        runs = self._run_repository.recent_runs(source, self._settings.baseline_runs + 1)
        latest, earlier = runs[0], runs[1:]
        if latest.error is not None:
            failures_in_a_row = next((i for i, run in enumerate(runs) if run.error is None), len(runs))
            return SourceHealth(
                source, HealthStatus.FAILING, f"failed {failures_in_a_row} run(s) in a row: {latest.error}"
            )
        baseline_counts = [run.fetched_jobs for run in earlier if run.error is None]
        if len(baseline_counts) < self._settings.min_runs_for_baseline:
            return SourceHealth(
                source, HealthStatus.OK, f"{latest.fetched_jobs} jobs (too few runs to compare yet)"
            )
        usual = statistics.median(baseline_counts)
        if usual > 0 and latest.fetched_jobs < usual * self._settings.drop_warning_ratio:
            return SourceHealth(
                source, HealthStatus.WARNING, f"{latest.fetched_jobs} jobs, against a usual {usual:.0f}"
            )
        return SourceHealth(source, HealthStatus.OK, f"{latest.fetched_jobs} jobs (usually {usual:.0f})")
