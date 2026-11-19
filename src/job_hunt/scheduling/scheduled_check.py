"""One scheduler check, started every few minutes by the backend. Most
checks find nothing due and end at once; when a slot is due it:

1. takes a lock, so a slow run and the next check never overlap;
2. waits for the internet (after a wake Wi-Fi can lag; if it isn't back,
   the slot stays due for the next check);
3. claims the due slots, then runs `job-hunt run` with the PATH saved when
   the schedule was set, the computer kept from sleeping while it can be
   (a laptop with its lid shut on battery sleeps anyway), and a limit on
   the time the computer is awake: a run paused by sleep carries on when it
   wakes, and only a run stuck while awake is stopped;
4. gives the slots back for a retry if the run fails, and shows a notification.
"""

import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

import httpx

from job_hunt.common.logger import get_logger
from job_hunt.common.settings_schema import SchedulingSettings
from job_hunt.scheduling.file_lock import exclusive_lock
from job_hunt.scheduling.schedule_backend import ScheduleBackend
from job_hunt.scheduling.schedule_store import ScheduleStore
from job_hunt.scheduling.slots import SlotHistory, due_slots, slot_id

logger = get_logger(__name__)

LOCK_FILE = "run.lock"
CONNECTIVITY_URL = "https://www.google.com"
SECONDS_BETWEEN_CONNECTIVITY_TRIES = 10
SECONDS_BETWEEN_POLLS = 15
SECONDS_TO_STOP = 30
# a run that outlives its limit
EXIT_TIMED_OUT = 142
# a run whose only failures were elsewhere (a source) isn't rerun
STEPS_WORTH_A_RETRY = frozenset({"LLM screening", "Google Sheet"})
# `job-hunt run` prints "ok      <step>  <summary>" or "FAILED  <step>  <summary>"
_STEP_LINE = re.compile(r"^(ok|FAILED)\s+(.+?)(?:\s{2,}(.*))?$")


class CheckOutcome(Enum):
    NOTHING_SCHEDULED = "nothing scheduled"
    NOTHING_DUE = "nothing due"
    BUSY = "a run is already in progress"
    OFFLINE = "no internet yet"
    RAN = "ran"
    FAILED = "run failed"


@dataclass(frozen=True)
class StepLine:
    ok: bool
    step: str
    summary: str


def read_step_lines(output: str) -> list[StepLine]:
    lines = []
    for line in output.splitlines():
        if match := _STEP_LINE.match(line.rstrip()):
            lines.append(StepLine(match.group(1) == "ok", match.group(2), match.group(3) or ""))
    return lines


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    # the run's step lines, from its output
    steps: list[StepLine]

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0

    # Worth running again: the run didn't get to the end (killed, timed out,
    # crashed), or what the user sees wasn't brought up to date (screening,
    # the sheet). A source or two failing is reported, not rerun: the next
    # day's run reads them again anyway.
    @property
    def needs_retry(self) -> bool:
        if self.succeeded:
            return False
        if self.exit_code == EXIT_TIMED_OUT or not self.steps:
            return True
        return any(not line.ok and line.step in STEPS_WORTH_A_RETRY for line in self.steps)

    def summary(self) -> str:
        if self.exit_code == EXIT_TIMED_OUT:
            return "stopped at the time limit before it finished"
        # killed, or crashed, before it printed what each step did
        if not self.steps:
            return f"stopped before it finished (exit {self.exit_code})"
        failed = [line.step for line in self.steps if not line.ok]
        fetch = next((line.summary for line in self.steps if line.step == "fetch"), "")
        new_jobs = re.search(r"\((\d+) new\)", fetch)
        found = f"{new_jobs.group(1)} new jobs" if new_jobs else "no new-jobs count"
        return f"{found}; failed: {', '.join(failed)}" if failed else f"{found}; every step ok"


def internet_is_up() -> bool:
    try:
        httpx.head(CONNECTIVITY_URL, timeout=5)
        return True
    except httpx.HTTPError:
        return False


# Runs command until it ends or limit_seconds of awake time pass: the
# monotonic clock stops while macOS and Linux sleep, so a run paused by a
# closed lid isn't killed for it (on 2026-11-18 one was, mid-fetch).
def run_with_time_limit(
    command: list[str],
    limit_seconds: float,
    log_path: Path,
    env: dict[str, str] | None = None,
    awake_clock: Callable[[], float] = time.monotonic,
    poll_seconds: float = SECONDS_BETWEEN_POLLS,
) -> RunResult:
    start = log_path.stat().st_size if log_path.exists() else 0
    with log_path.open("a") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env)
        deadline = awake_clock() + limit_seconds
        exit_code = None
        while (exit_code := process.poll()) is None:
            if awake_clock() >= deadline:
                process.terminate()
                try:
                    process.wait(SECONDS_TO_STOP)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                exit_code = EXIT_TIMED_OUT
                break
            time.sleep(poll_seconds)
    with log_path.open() as log:
        log.seek(start)
        return RunResult(exit_code, read_step_lines(log.read()))


class ScheduledCheck:
    def __init__(
        self,
        settings: SchedulingSettings,
        store: ScheduleStore,
        backend: ScheduleBackend,
        run_command: list[str],
        clock: Callable[[], datetime] = datetime.now,
        online: Callable[[], bool] = internet_is_up,
        run: Callable[[list[str], float, Path, dict[str, str]], RunResult] = run_with_time_limit,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._settings = settings
        self._store = store
        self._backend = backend
        self._run_command = run_command
        self._clock = clock
        self._online = online
        self._run = run
        self._sleep = sleep

    def check(self) -> CheckOutcome:
        spec = self._store.load_spec()
        if spec is None:
            return CheckOutcome.NOTHING_SCHEDULED
        history = self._store.load_history()
        grace = timedelta(minutes=self._settings.on_time_grace_minutes)
        if not due_slots(spec, self._clock(), history, grace):
            return CheckOutcome.NOTHING_DUE
        with exclusive_lock(self._store.state_dir / LOCK_FILE) as locked:
            if not locked:
                return CheckOutcome.BUSY
            if not self._wait_for_internet():
                self._log("no internet yet; the slot stays due for the next check")
                return CheckOutcome.OFFLINE
            # read again under the lock: a run that just ended may have taken these
            history = self._store.load_history()
            due = due_slots(spec, self._clock(), history, grace)
            if not due:
                return CheckOutcome.NOTHING_DUE
            return self._run_slots(due, history)

    def _run_slots(self, due: list[datetime], history: SlotHistory) -> CheckOutcome:
        # claimed first, so a crash midway can't start the same slot twice in a loop
        history.claim(due)
        history.prune(self._clock())
        self._store.save_history(history)
        slots = ", ".join(slot_id(slot) for slot in due)
        self._log(f"run started for {slots}")
        last_run = {"slots": slots, "started": self._clock().isoformat(timespec="seconds"), "finished": None}
        self._store.save_last_run({**last_run, "outcome": "running", "summary": ""})
        limit = self._settings.run_time_limit_minutes * 60
        with self._backend.keep_awake():
            result = self._run(self._run_command, limit, self._log_path(), self._run_environment())
        self._store.save_last_run(
            {
                **last_run,
                "finished": self._clock().isoformat(timespec="seconds"),
                "outcome": "ok" if result.succeeded else "failed",
                "summary": result.summary(),
            }
        )
        if result.succeeded:
            self._log(f"run finished: {result.summary()}")
            self._backend.notify("Job hunt", f"Run finished: {result.summary()}")
            return CheckOutcome.RAN
        if not result.needs_retry:
            self._log(f"run finished with problems: {result.summary()}; not rerun (see the log)")
            self._backend.notify(
                "Job hunt", f"Run finished with problems: {result.summary()}. Log: {self._log_path()}"
            )
            return CheckOutcome.RAN
        history = self._store.load_history()
        released = history.record_failure(due, self._settings.attempts_per_slot)
        self._store.save_history(history)
        retry = "retrying at the next check" if released else "no retries left for this slot"
        self._log(f"run failed (exit {result.exit_code}): {result.summary()}; {retry}")
        self._backend.notify("Job hunt", f"Run failed: {result.summary()}; {retry}. Log: {self._log_path()}")
        return CheckOutcome.FAILED

    # True while a run holds the lock (another process, or this one)
    def run_in_progress(self) -> bool:
        with exclusive_lock(self._store.state_dir / LOCK_FILE) as locked:
            return not locked

    def _wait_for_internet(self) -> bool:
        tries = max(1, self._settings.internet_wait_seconds // SECONDS_BETWEEN_CONNECTIVITY_TRIES)
        for attempt in range(tries):
            if self._online():
                return True
            if attempt < tries - 1:
                self._sleep(SECONDS_BETWEEN_CONNECTIVITY_TRIES)
        return False

    # this environment, with the saved folders first on PATH
    def _run_environment(self) -> dict[str, str]:
        folders = [*self._store.load_run_path(), *os.environ.get("PATH", "").split(os.pathsep)]
        return {**os.environ, "PATH": os.pathsep.join(dict.fromkeys(folder for folder in folders if folder))}

    def _log_path(self) -> Path:
        self._settings.log_dir.mkdir(parents=True, exist_ok=True)
        return self._settings.log_dir / f"{self._clock():%Y-%m-%d}.log"

    def _log(self, message: str) -> None:
        logger.info("scheduler: %s", message)
        with self._log_path().open("a") as log:
            log.write(f"══ {self._clock():%Y-%m-%d %H:%M:%S} {message}\n")
