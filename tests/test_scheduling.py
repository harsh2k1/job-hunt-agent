import fcntl
import plistlib
import sys
from contextlib import contextmanager
from datetime import datetime, time, timedelta
from pathlib import Path

import pytest

from job_hunt.common.settings import settings
from job_hunt.scheduling.launchd_backend import app_source, launch_agent_plist, pmset_commands, wake_times
from job_hunt.scheduling.schedule_backend import ScheduleBackend
from job_hunt.scheduling.schedule_spec import InvalidTimeError, ScheduleSpec, evenly_spaced, parse_clock_time
from job_hunt.scheduling.schedule_store import ScheduleStore
from job_hunt.scheduling.scheduled_check import (
    EXIT_TIMED_OUT,
    LOCK_FILE,
    CheckOutcome,
    RunResult,
    ScheduledCheck,
    StepLine,
    read_step_lines,
    run_with_time_limit,
)
from job_hunt.scheduling.slots import SlotHistory, due_slots

GRACE = timedelta(minutes=15)
SET_ON = datetime(2026, 11, 17, 12, 0)


def spec(*times: str, catch_up: bool = True, wake: bool = False) -> ScheduleSpec:
    return ScheduleSpec(
        times=tuple(parse_clock_time(text) for text in times),
        catch_up=catch_up,
        wake=wake,
        active_since=SET_ON,
    )


@pytest.mark.parametrize(
    "text, expected",
    [("03:00", time(3)), ("3:05", time(3, 5)), ("3am", time(3)), ("9pm", time(21)), ("9:30 PM", time(21, 30)),
     ("12am", time(0)), ("12pm", time(12)), ("21:00", time(21))],
)  # fmt: skip
def test_times_are_read_as_people_write_them(text, expected):
    assert parse_clock_time(text) == expected


@pytest.mark.parametrize("text", ["25:00", "13pm", "3:75", "noon", ""])
def test_a_time_that_isnt_one_is_refused(text):
    with pytest.raises(InvalidTimeError):
        parse_clock_time(text)


def test_runs_per_day_are_spread_evenly_from_the_first():
    assert evenly_spaced(3, time(3)) == (time(3), time(11), time(19))
    # past midnight wraps round; the schedule keeps them in clock order
    assert evenly_spaced(2, time(21)) == (time(21), time(9))
    assert ScheduleSpec(
        times=evenly_spaced(2, time(21)), catch_up=True, wake=False, active_since=SET_ON
    ).times == (
        time(9),
        time(21),
    )


def test_a_slot_is_due_once_its_time_comes():
    schedule = spec("03:00")
    assert due_slots(schedule, datetime(2026, 11, 18, 2, 59), SlotHistory(), GRACE) == []
    assert due_slots(schedule, datetime(2026, 11, 18, 3, 1), SlotHistory(), GRACE) == [
        datetime(2026, 11, 18, 3)
    ]


def test_with_catch_up_a_slot_missed_asleep_runs_on_waking_even_the_next_morning():
    # asleep from 20:30 to 08:10: last night's 21:00 and this morning's 03:00 are both due, in one run
    due = due_slots(spec("03:00", "21:00"), datetime(2026, 11, 18, 8, 10), SlotHistory(), GRACE)
    assert due == [datetime(2026, 11, 17, 21), datetime(2026, 11, 18, 3)]


def test_without_catch_up_a_missed_slot_is_skipped_but_a_check_a_little_late_still_runs_it():
    schedule = spec("03:00", catch_up=False)
    assert due_slots(schedule, datetime(2026, 11, 18, 8, 10), SlotHistory(), GRACE) == []
    # the first check after 03:00 can come a few minutes late
    assert due_slots(schedule, datetime(2026, 11, 18, 3, 4), SlotHistory(), GRACE) == [
        datetime(2026, 11, 18, 3)
    ]


def test_slots_already_run_or_before_the_schedule_was_set_are_not_due():
    # set at 12:00 on the 17th: that morning's 03:00 never counts as missed
    assert due_slots(spec("03:00"), datetime(2026, 11, 17, 12, 30), SlotHistory(), GRACE) == []
    history = SlotHistory()
    history.claim([datetime(2026, 11, 18, 3)])
    assert due_slots(spec("03:00"), datetime(2026, 11, 18, 9), history, GRACE) == []


def test_a_failed_slot_is_retried_until_its_attempts_run_out():
    slot = datetime(2026, 11, 18, 3)
    history = SlotHistory()
    for attempt in range(1, 4):
        history.claim([slot])
        released = history.record_failure([slot], max_attempts=3)
        assert released == ([slot] if attempt < 3 else [])
    assert "2026-11-18 03:00" in history.claimed


def test_the_store_keeps_the_schedule_and_history(tmp_path):
    store = ScheduleStore(tmp_path)
    store.save_spec(spec("03:00", "9pm", wake=True))
    assert store.load_spec() == spec("03:00", "21:00", wake=True)
    history = SlotHistory(attempts={"2026-11-18 03:00": 1})
    history.claim([datetime(2026, 11, 18, 3)])
    store.save_history(history)
    assert store.load_history() == history


class FakeBackend(ScheduleBackend):
    name = "fake"

    def __init__(self, store, wakes_until=None):
        super().__init__(settings.scheduling, store)
        self.notes: list[str] = []
        self.awake = False
        self._wakes_until = wakes_until

    def install(self, check_command, path_dirs, spec, now):
        return []

    def uninstall(self):
        return []

    def describe(self, now):
        return []

    def wakes_booked_until(self, now):
        return self._wakes_until

    @contextmanager
    def keep_awake(self):
        self.awake = True
        yield
        self.awake = False

    def notify(self, title, message):
        self.notes.append(message)


class FakeRun:
    def __init__(self, exit_code=0):
        self.calls = 0
        self.exit_code = exit_code

    def __call__(self, command, limit_seconds, log_path):
        self.calls += 1
        return RunResult(self.exit_code, [StepLine(True, "fetch", "40 sources, 900 jobs (23 new)")])


def make_check(tmp_path, now, run, online=lambda: True, schedule=None, backend=None):
    store = ScheduleStore(tmp_path / "state")
    store.save_spec(schedule or spec("03:00"))
    scheduling = settings.scheduling.model_copy(
        update={"state_dir": tmp_path / "state", "log_dir": tmp_path / "logs"}
    )
    backend = backend or FakeBackend(store)
    check = ScheduledCheck(scheduling, store, backend, ["job-hunt", "run"], clock=lambda: now, online=online,
                           run=run, sleep=lambda seconds: None)  # fmt: skip
    return check, store, backend


def test_a_due_slot_runs_once_and_says_what_it_found(tmp_path):
    run = FakeRun()
    check, store, backend = make_check(tmp_path, datetime(2026, 11, 18, 3, 2), run)
    assert check.check() is CheckOutcome.RAN
    assert check.check() is CheckOutcome.NOTHING_DUE
    assert run.calls == 1
    assert backend.notes == ["Run finished: 23 new jobs; every step ok"]
    assert "run finished: 23 new jobs" in (tmp_path / "logs" / "2026-11-18.log").read_text()


def test_a_failed_run_is_retried_at_the_next_check(tmp_path):
    run = FakeRun(exit_code=1)
    check, store, backend = make_check(tmp_path, datetime(2026, 11, 18, 3, 2), run)
    assert check.check() is CheckOutcome.FAILED
    assert "retrying at the next check" in backend.notes[0]
    assert check.check() is CheckOutcome.FAILED
    # the third failure is the last attempt: nothing is due after it
    assert check.check() is CheckOutcome.FAILED
    assert check.check() is CheckOutcome.NOTHING_DUE
    assert run.calls == 3


def test_without_internet_the_slot_stays_due(tmp_path):
    run = FakeRun()
    check, store, _ = make_check(tmp_path, datetime(2026, 11, 18, 3, 2), run, online=lambda: False)
    assert check.check() is CheckOutcome.OFFLINE
    assert run.calls == 0
    assert due_slots(store.load_spec(), datetime(2026, 11, 18, 3, 7), store.load_history(), GRACE)


def test_a_check_while_a_run_is_in_progress_leaves_it_alone(tmp_path):
    run = FakeRun()
    check, store, _ = make_check(tmp_path, datetime(2026, 11, 18, 3, 2), run)
    (tmp_path / "state").mkdir(exist_ok=True)
    with (tmp_path / "state" / LOCK_FILE).open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert check.check() is CheckOutcome.BUSY
    assert run.calls == 0


def test_the_machine_is_kept_awake_during_the_run(tmp_path):
    seen = []

    class AwakeRun(FakeRun):
        def __call__(self, command, limit_seconds, log_path):
            seen.append(backend_ref.awake)
            return super().__call__(command, limit_seconds, log_path)

    check, _, backend_ref = make_check(tmp_path, datetime(2026, 11, 18, 3, 2), AwakeRun())
    check.check()
    assert seen == [True] and backend_ref.awake is False


def test_a_reminder_comes_once_a_day_when_booked_wakes_run_low(tmp_path):
    store = ScheduleStore(tmp_path / "state")
    backend = FakeBackend(store, wakes_until=datetime(2026, 11, 20))
    check, _, _ = make_check(
        tmp_path, datetime(2026, 11, 17, 13), FakeRun(), schedule=spec("03:00", wake=True), backend=backend
    )
    check.check()
    check.check()
    assert backend.notes == ["Booked wakes run out soon: run `job-hunt schedule renew` in Terminal."]


def test_step_lines_from_the_daily_run_are_read():
    output = (
        "2026-11-18 03:05:00 INFO [job_hunt] daily run: fetch\n"
        "ok      fetch                    40 sources, 900 jobs (23 new), 2 companies added\n"
        "FAILED  Google Sheet             failed: quota\n"
        "ok      Common Crawl discovery   nothing new\n"
    )
    steps = read_step_lines(output)
    assert [(step.ok, step.step) for step in steps] == [
        (True, "fetch"),
        (False, "Google Sheet"),
        (True, "Common Crawl discovery"),
    ]
    assert RunResult(1, steps).summary() == "23 new jobs; failed: Google Sheet"


def test_a_run_that_outlives_its_wall_clock_limit_is_stopped(tmp_path):
    # the wall clock jumps an hour, as it does across a sleep, while the run hangs
    readings = iter([0.0, 3600.0])
    result = run_with_time_limit(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        limit_seconds=1800,
        log_path=tmp_path / "run.log",
        wall_clock=lambda: next(readings, 3600.0),
        poll_seconds=0.05,
    )
    assert result.exit_code == EXIT_TIMED_OUT


def test_a_run_s_output_goes_to_the_log_and_its_steps_are_read(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("earlier run\nok      fetch                    old (99 new)\n")
    script = "print('ok      fetch                    40 sources, 900 jobs (5 new)')"
    result = run_with_time_limit([sys.executable, "-c", script], 60, log, poll_seconds=0.05)
    assert result.exit_code == 0
    # only this run's lines count
    assert result.summary() == "5 new jobs; every step ok"
    assert "earlier run" in log.read_text()


def test_the_app_runs_the_check_with_a_quoted_path():
    source = app_source(
        ["/Users/me/My venv/bin/python", "-m", "job_hunt", "schedule", "check"], ["/Users/me/My venv/bin"]
    )
    assert source == (
        "do shell script \"export PATH='/Users/me/My venv/bin'; "
        "'/Users/me/My venv/bin/python' -m job_hunt schedule check >/dev/null 2>&1\"\n"
    )


def test_the_launch_agent_checks_every_interval_and_at_login():
    plist = plistlib.loads(launch_agent_plist("com.jobhunt.test", Path("/Apps/Sched.app"), 5, Path("/logs")))
    assert plist["ProgramArguments"] == ["/Apps/Sched.app/Contents/MacOS/applet"]
    assert (plist["StartInterval"], plist["RunAtLoad"]) == (300, True)


def test_wakes_are_booked_shortly_before_each_future_slot():
    wakes = wake_times(spec("03:00", "21:00"), datetime(2026, 11, 17, 12), days_ahead=1, minutes_before=2)
    assert wakes == [
        datetime(2026, 11, 17, 20, 58),
        datetime(2026, 11, 18, 2, 58),
        datetime(2026, 11, 18, 20, 58),
    ]
    assert pmset_commands("book", wakes[:1]) == ["pmset schedule wake '11/17/26 20:58:00' job-hunt"]
    assert pmset_commands("cancel", wakes[:1]) == ["pmset schedule cancel wake '11/17/26 20:58:00' job-hunt"]
