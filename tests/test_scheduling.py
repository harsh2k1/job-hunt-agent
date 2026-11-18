import os
import plistlib
import sys
import xml.etree.ElementTree as ElementTree
from contextlib import contextmanager
from datetime import datetime, time, timedelta
from pathlib import Path

import pytest

from job_hunt.common.settings import settings
from job_hunt.scheduling.file_lock import exclusive_lock
from job_hunt.scheduling.launchd_backend import app_source, launch_agent_plist
from job_hunt.scheduling.schedule_backend import ScheduleBackend, ScheduleBackendError, backend_name
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
from job_hunt.scheduling.systemd_backend import service_unit, systemd_command_line, timer_unit
from job_hunt.scheduling.task_scheduler_backend import task_xml

GRACE = timedelta(minutes=15)
SET_ON = datetime(2026, 11, 17, 12, 0)
TASK_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def spec(*times: str, catch_up: bool = False) -> ScheduleSpec:
    return ScheduleSpec(
        times=tuple(parse_clock_time(text) for text in times), catch_up=catch_up, active_since=SET_ON
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
    assert ScheduleSpec(times=evenly_spaced(2, time(21)), catch_up=False, active_since=SET_ON).times == (
        time(9),
        time(21),
    )


def test_a_slot_is_due_once_its_time_comes():
    schedule = spec("03:00")
    assert due_slots(schedule, datetime(2026, 11, 18, 2, 59), SlotHistory(), GRACE) == []
    assert due_slots(schedule, datetime(2026, 11, 18, 3, 1), SlotHistory(), GRACE) == [
        datetime(2026, 11, 18, 3)
    ]


def test_by_default_a_slot_missed_asleep_is_skipped_but_a_check_a_little_late_still_runs_it():
    schedule = spec("03:00")
    # woke at 08:10: 03:00 is skipped
    assert due_slots(schedule, datetime(2026, 11, 18, 8, 10), SlotHistory(), GRACE) == []
    # the first check after 03:00 can come a few minutes late
    assert due_slots(schedule, datetime(2026, 11, 18, 3, 4), SlotHistory(), GRACE) == [
        datetime(2026, 11, 18, 3)
    ]


def test_with_run_on_wake_a_slot_missed_asleep_runs_on_waking_even_the_next_morning():
    # asleep from 20:30 to 08:10: last night's 21:00 and this morning's 03:00 are both due, in one run
    due = due_slots(
        spec("03:00", "21:00", catch_up=True), datetime(2026, 11, 18, 8, 10), SlotHistory(), GRACE
    )
    assert due == [datetime(2026, 11, 17, 21), datetime(2026, 11, 18, 3)]


def test_slots_already_run_or_before_the_schedule_was_set_are_not_due():
    # set at 12:00 on the 17th: that morning's 03:00 never counts as missed
    assert due_slots(spec("03:00", catch_up=True), datetime(2026, 11, 17, 12, 30), SlotHistory(), GRACE) == []
    history = SlotHistory()
    history.claim([datetime(2026, 11, 18, 3)])
    assert due_slots(spec("03:00", catch_up=True), datetime(2026, 11, 18, 9), history, GRACE) == []


def test_a_failed_slot_is_retried_until_its_attempts_run_out():
    slot = datetime(2026, 11, 18, 3)
    history = SlotHistory()
    for attempt in range(1, 4):
        history.claim([slot])
        released = history.record_failure([slot], max_attempts=3)
        assert released == ([slot] if attempt < 3 else [])
    assert "2026-11-18 03:00" in history.claimed


def test_the_store_keeps_the_schedule_history_and_run_path(tmp_path):
    store = ScheduleStore(tmp_path)
    store.save_spec(spec("03:00", "9pm", catch_up=True))
    assert store.load_spec() == spec("03:00", "21:00", catch_up=True)
    history = SlotHistory(attempts={"2026-11-18 03:00": 1})
    history.claim([datetime(2026, 11, 18, 3)])
    store.save_history(history)
    assert store.load_history() == history
    store.save_run_path(["/venv/bin", "/opt/homebrew/bin"])
    assert store.load_run_path() == ["/venv/bin", "/opt/homebrew/bin"]


@pytest.mark.parametrize(
    "platform, backend", [("darwin", "launchd"), ("linux", "systemd"), ("win32", "task_scheduler")]
)
def test_auto_picks_the_scheduler_of_each_system(platform, backend):
    assert backend_name(settings.scheduling.model_copy(update={"backend": "auto"}), platform) == backend


def test_an_unknown_system_is_told_to_choose_a_backend():
    with pytest.raises(ScheduleBackendError):
        backend_name(settings.scheduling.model_copy(update={"backend": "auto"}), "aix")


class FakeBackend(ScheduleBackend):
    name = "fake"

    def __init__(self, store):
        super().__init__(settings.scheduling, store)
        self.notes: list[str] = []
        self.awake = False

    def install(self, check_command, times):
        return []

    def uninstall(self):
        return None

    def describe(self):
        return []

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
        self.backend: FakeBackend | None = None
        self.awake_during: list[bool] = []
        self.paths: list[str] = []

    def __call__(self, command, limit_seconds, log_path, env):
        self.calls += 1
        self.paths.append(env["PATH"])
        if self.backend:
            self.awake_during.append(self.backend.awake)
        return RunResult(self.exit_code, [StepLine(True, "fetch", "40 sources, 900 jobs (23 new)")])


def make_check(tmp_path, now, run, online=lambda: True, schedule=None):
    store = ScheduleStore(tmp_path / "state")
    store.save_spec(schedule or spec("03:00"))
    scheduling = settings.scheduling.model_copy(
        update={"state_dir": tmp_path / "state", "log_dir": tmp_path / "logs"}
    )
    backend = FakeBackend(store)
    run.backend = backend
    check = ScheduledCheck(scheduling, store, backend, ["job-hunt", "run"], clock=lambda: now, online=online,
                           run=run, sleep=lambda seconds: None)  # fmt: skip
    return check, store, backend


def test_a_due_slot_runs_once_kept_awake_and_says_what_it_found(tmp_path):
    run = FakeRun()
    check, store, backend = make_check(tmp_path, datetime(2026, 11, 18, 3, 2), run)
    assert check.check() is CheckOutcome.RAN
    assert check.check() is CheckOutcome.NOTHING_DUE
    assert run.calls == 1
    assert run.awake_during == [True] and backend.awake is False
    assert backend.notes == ["Run finished: 23 new jobs; every step ok"]
    assert "run finished: 23 new jobs" in (tmp_path / "logs" / "2026-11-18.log").read_text()


def test_the_run_gets_the_path_saved_when_the_schedule_was_set(tmp_path):
    run = FakeRun()
    check, store, _ = make_check(tmp_path, datetime(2026, 11, 18, 3, 2), run)
    store.save_run_path(["/venv/bin", "/opt/homebrew/bin"])
    check.check()
    assert run.paths[0].split(os.pathsep)[:2] == ["/venv/bin", "/opt/homebrew/bin"]


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
    check, _, _ = make_check(tmp_path, datetime(2026, 11, 18, 3, 2), run)
    with exclusive_lock(tmp_path / "state" / LOCK_FILE) as held:
        assert held
        assert check.check() is CheckOutcome.BUSY
    assert run.calls == 0
    # released when the holder lets go
    assert check.check() is CheckOutcome.RAN


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


def test_a_runs_output_goes_to_the_log_and_only_its_own_steps_are_read(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("earlier run\nok      fetch                    old (99 new)\n")
    script = "print('ok      fetch                    40 sources, 900 jobs (5 new)')"
    result = run_with_time_limit([sys.executable, "-c", script], 60, log, poll_seconds=0.05)
    assert result.exit_code == 0
    assert result.summary() == "5 new jobs; every step ok"
    assert "earlier run" in log.read_text()


def test_macos_the_app_runs_the_check_with_quoted_paths_and_never_shows_an_error_dialog():
    source = app_source(["/Users/me/My venv/bin/python", "-m", "job_hunt", "schedule", "check"])
    assert source == (
        "try\n"
        "\tdo shell script \"'/Users/me/My venv/bin/python' -m job_hunt schedule check >/dev/null 2>&1\"\n"
        "end try\n"
    )


def test_macos_the_launch_agent_checks_every_interval_and_at_login():
    plist = plistlib.loads(
        launch_agent_plist(
            "com.jobhunt.test", Path("/Apps/Sched.app"), 5, Path("/logs"), (time(4), time(21, 30))
        )
    )
    assert plist["ProgramArguments"] == ["/Apps/Sched.app/Contents/MacOS/applet"]
    # on time at each run time, and every 5 minutes as a backup
    assert plist["StartCalendarInterval"] == [{"Hour": 4, "Minute": 0}, {"Hour": 21, "Minute": 30}]
    assert (plist["StartInterval"], plist["RunAtLoad"]) == (300, True)


def test_linux_the_timer_checks_every_interval_catches_up_after_suspend_and_never_wakes():
    timer = timer_unit(5, (time(4), time(21, 30)))
    assert "OnCalendar=*-*-* 04:00:00\nOnCalendar=*-*-* 21:30:00\nOnCalendar=*:0/5\n" in timer
    assert "Persistent=true\n" in timer
    assert "WakeSystem" not in timer
    service = service_unit(
        ["/home/me/my venv/bin/python", "-m", "job_hunt", "schedule", "check"], Path("/logs")
    )
    assert 'ExecStart="/home/me/my venv/bin/python" "-m" "job_hunt" "schedule" "check"\n' in service
    assert "StandardOutput=append:/logs/systemd.log\n" in service


def test_linux_a_percent_or_quote_in_a_command_is_kept_literal():
    assert systemd_command_line(["/opt/100%/python", 'say "hi"']) == '"/opt/100%%/python" "say \\"hi\\""'


def test_windows_the_task_repeats_runs_after_a_missed_start_and_never_wakes_the_pc():
    # read as Task Scheduler reads it: UTF-16
    xml = task_xml(
        [r"C:\Users\me\venv\Scripts\pythonw.exe", "-m", "job_hunt", "schedule", "check"], 5, (time(4),)
    )
    root = ElementTree.fromstring(xml.encode("utf-16"))
    assert root.findtext(".//t:Repetition/t:Interval", namespaces=TASK_NS) == "PT5M"
    assert root.findtext(".//t:CalendarTrigger/t:StartBoundary", namespaces=TASK_NS) == "2026-01-01T04:00:00"
    assert root.find(".//t:LogonTrigger", TASK_NS) is not None
    assert root.findtext(".//t:StartWhenAvailable", namespaces=TASK_NS) == "true"
    assert root.findtext(".//t:WakeToRun", namespaces=TASK_NS) == "false"
    assert root.findtext(".//t:Exec/t:Command", namespaces=TASK_NS) == r"C:\Users\me\venv\Scripts\pythonw.exe"
    assert root.findtext(".//t:Exec/t:Arguments", namespaces=TASK_NS) == "-m job_hunt schedule check"


def test_a_run_is_recorded_as_running_then_with_its_outcome(tmp_path):
    seen_while_running = []

    class WatchingRun(FakeRun):
        def __call__(self, command, limit_seconds, log_path, env):
            seen_while_running.append(store.load_last_run())
            return super().__call__(command, limit_seconds, log_path, env)

    check, store, _ = make_check(
        tmp_path, datetime(2026, 11, 18, 4, 4), WatchingRun(), schedule=spec("04:00")
    )
    check.check()
    assert seen_while_running[0]["outcome"] == "running" and seen_while_running[0]["finished"] is None
    last = store.load_last_run()
    assert (last["slots"], last["outcome"], last["summary"]) == (
        "2026-11-18 04:00",
        "ok",
        "23 new jobs; every step ok",
    )


def test_status_says_a_run_is_going_or_how_the_last_one_ended_with_the_command_for_its_log(tmp_path):
    from job_hunt.cli.schedule_commands import _last_run_line

    log = tmp_path / "2026-11-18.log"
    running = {"slots": "2026-11-18 04:00", "started": "2026-11-18T04:04:05", "finished": None,
               "outcome": "running", "summary": ""}  # fmt: skip
    assert _last_run_line(running, True, tmp_path) == (
        f"Running now: started 2026-11-18 04:04 for slot 2026-11-18 04:00. Follow it: tail -f {log}"
    )
    # started by an older version that kept no record: the lock tells, and the newest log is the one
    (tmp_path / "2026-11-17.log").write_text("")
    log.write_text("")
    assert _last_run_line(None, True, tmp_path) == f"Running now. Follow it: tail -f {log}"
    assert "stopped without finishing" in _last_run_line(running, False, tmp_path)
    done = {
        **running,
        "finished": "2026-11-18T04:41:00",
        "outcome": "ok",
        "summary": "23 new jobs; every step ok",
    }
    assert _last_run_line(done, False, tmp_path) == (
        "Last run: ok, 2026-11-18 04:04-04:41 (slot 2026-11-18 04:00): 23 new jobs; every step ok. "
        f"Log: tail -n 50 {log}"
    )
