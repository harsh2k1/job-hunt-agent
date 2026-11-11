from datetime import datetime, timedelta, timezone

import pytest

from job_hunt.common.settings import settings
from job_hunt.job_pipeline.source_health import HealthStatus, SourceHealthCheck
from job_hunt.job_store.models import SourceRun
from job_hunt.job_store.source_run_repository import SourceRunRepository

START = datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc)


@pytest.fixture
def runs(database) -> SourceRunRepository:
    return SourceRunRepository(database)


def record(runs: SourceRunRepository, source: str, counts: list[int | str]) -> None:
    """One run a day, oldest first; a string is a failed run's error."""
    for day, count in enumerate(counts):
        failed = isinstance(count, str)
        runs.record(
            SourceRun(
                source=source,
                started_at=START + timedelta(days=day),
                finished_at=START + timedelta(days=day, minutes=5),
                fetched_jobs=0 if failed else count,
                error=count if failed else None,
            )
        )


def check(runs: SourceRunRepository, source: str):
    return SourceHealthCheck(settings.source_health, runs).check(source)


def test_a_usual_count_is_ok(runs):
    record(runs, "greenhouse", [2800, 2750, 2900, 2820])
    assert check(runs, "greenhouse").status is HealthStatus.OK


def test_a_sharp_drop_is_a_warning(runs):
    record(runs, "linkedin", [120, 110, 130, 12])
    health = check(runs, "linkedin")
    assert health.status is HealthStatus.WARNING
    assert "usual 120" in health.detail


def test_a_failed_run_is_failing_with_the_streak_and_reason(runs):
    record(runs, "kcl_target_connect", [300, "session ended", "session ended"])
    health = check(runs, "kcl_target_connect")
    assert health.status is HealthStatus.FAILING
    assert "2 run(s) in a row: session ended" in health.detail


def test_a_new_source_isnt_judged_before_it_has_history(runs):
    record(runs, "meta_careers", [81, 2])
    assert check(runs, "meta_careers").status is HealthStatus.OK


def test_failed_runs_dont_drag_the_usual_count_down(runs):
    record(runs, "reed", [270, "timeout", 260, 280, 265])
    assert check(runs, "reed").status is HealthStatus.OK


def test_every_source_with_runs_is_checked(runs):
    record(runs, "reed", [270])
    record(runs, "adzuna", ["no app_id"])
    assert {
        health.source: health.status for health in SourceHealthCheck(settings.source_health, runs).check_all()
    } == {
        "adzuna": HealthStatus.FAILING,
        "reed": HealthStatus.OK,
    }
