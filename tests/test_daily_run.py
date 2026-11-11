from job_hunt.job_pipeline.daily_run import DailyRun, DailyRunStep, StepOutcome


class FixedStep(DailyRunStep):
    def __init__(self, name: str, succeeded: bool = True, error: Exception | None = None):
        self.name = name
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
