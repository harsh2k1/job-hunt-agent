from datetime import date

from job_hunt.job_pipeline.daily_run import DailyRun, DailyRunStep, StepOutcome, StepRunLog


class FixedStep(DailyRunStep):
    def __init__(self, name: str, succeeded: bool = True, error: Exception | None = None):
        self.name = name
        self.key = name
        self._succeeded = succeeded
        self._error = error
        self.ran = False

    def run(self) -> StepOutcome:
        self.ran = True
        if self._error is not None:
            raise self._error
        return StepOutcome(self.name, self._succeeded, "done")


def test_a_broken_step_is_reported_and_the_rest_still_run():
    steps = [FixedStep("discovery", error=RuntimeError("search engine down")), FixedStep("fetch")]
    outcomes = DailyRun(steps).run()

    assert [(outcome.step, outcome.succeeded) for outcome in outcomes] == [
        ("discovery", False),
        ("fetch", True),
    ]
    assert outcomes[0].summary == "failed: search engine down"
    assert steps[1].ran


def test_steps_run_in_order_and_skipped_ones_are_left_out():
    run = DailyRun.from_settings(include_discovery=False, include_sponsor_register=False)
    assert run.step_names == ["fetch", "source health", "LLM screening", "Google Sheet"]


def run_on(day: date, steps, every_days, log):
    return DailyRun(steps, every_days, log, today=day).run()


def test_a_step_runs_only_as_often_as_set(tmp_path):
    log = StepRunLog(tmp_path / "state.json")
    every_days = {"crawl": 3, "fetch": 1}
    monday = date(2026, 11, 16)
    for offset, crawl_runs in [(0, True), (1, False), (2, False), (3, True)]:
        steps = [FixedStep("crawl"), FixedStep("fetch")]
        outcomes = run_on(date.fromordinal(monday.toordinal() + offset), steps, every_days, log)
        assert (steps[0].ran, steps[1].ran) == (crawl_runs, True)
        assert outcomes[0].skipped is not crawl_runs
    assert outcomes[0].summary == "done"


def test_a_step_not_due_says_when_it_last_ran(tmp_path):
    log = StepRunLog(tmp_path / "state.json")
    run_on(date(2026, 11, 16), [FixedStep("crawl")], {"crawl": 3}, log)
    [outcome] = run_on(date(2026, 11, 17), [FixedStep("crawl")], {"crawl": 3}, log)
    assert (outcome.skipped, outcome.succeeded) == (True, True)
    assert outcome.summary == "not due: every 3 days, last run 2026-11-16"


def test_a_failed_step_is_due_again_next_run(tmp_path):
    log = StepRunLog(tmp_path / "state.json")
    run_on(date(2026, 11, 16), [FixedStep("crawl", error=RuntimeError("down"))], {"crawl": 3}, log)
    step = FixedStep("crawl")
    run_on(date(2026, 11, 17), [step], {"crawl": 3}, log)
    assert step.ran


def test_all_steps_runs_everything_whenever(tmp_path):
    log = StepRunLog(tmp_path / "state.json")
    run_on(date(2026, 11, 16), [FixedStep("crawl")], {"crawl": 3}, log)
    step = FixedStep("crawl")
    DailyRun([step], None, log, today=date(2026, 11, 17)).run()
    assert step.ran


def test_every_step_has_its_line_in_the_settings():
    from job_hunt.common.settings import settings

    run = DailyRun.from_settings()
    assert {step.key for step in run._steps} <= set(settings.daily_run.every_days.model_dump())
