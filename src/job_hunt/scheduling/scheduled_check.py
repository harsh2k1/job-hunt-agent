"""One scheduler check, started every few minutes by the backend. Most
checks find nothing due and end at once; when a slot is due it:

1. takes a lock, so a slow run and the next check never overlap;
2. waits for the internet (after a wake Wi-Fi can lag; if it isn't back,
   the slot stays due for the next check);
3. claims the due slots, then runs `job-hunt run` with the machine kept
   awake and a wall-clock time limit (a hung call can't hold the lock for
   days; time asleep counts, unlike Python's own timeouts);
4. gives the slots back for a retry if the run fails, and shows a notification.
"""

import fcntl
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
# remind to book more wakes when fewer days than this are left
WAKE_REMINDER_DAYS = 7
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

    def summary(self) -> str:
        failed = [line.step for line in self.steps if not line.ok]
        fetch = next((line.summary for line in self.steps if line.step == "fetch"), "")
        new_jobs = re.search(r"\((\d+) new\)", fetch)
        found = f"{new_jobs.group(1)} new jobs" if new_jobs else "no fetch summary"
        if self.exit_code == EXIT_TIMED_OUT:
            return f"stopped at the time limit; {found}"
        return f"{found}; failed: {', '.join(failed)}" if failed else f"{found}; every step ok"


def internet_is_up() -> bool:
    try:
        httpx.head(CONNECTIVITY_URL, timeout=5)
        return True
    except httpx.HTTPError:
        return False


# Runs command until it ends or limit_seconds of wall-clock time pass
# (time.time, which counts sleep; subprocess timeouts don't).
def run_with_time_limit(
    command: list[str],
    limit_seconds: float,
    log_path: Path,
    wall_clock: Callable[[], float] = time.time,
    poll_seconds: float = SECONDS_BETWEEN_POLLS,
) -> RunResult:
    start = log_path.stat().st_size if log_path.exists() else 0
    with log_path.open("a") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        deadline = wall_clock() + limit_seconds
        exit_code = None
        while (exit_code := process.poll()) is None:
            if wall_clock() >= deadline:
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
        run: Callable[[list[str], float, Path], RunResult] = run_with_time_limit,
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
            self._remind_to_book_wakes(spec.wake, history)
            return CheckOutcome.NOTHING_DUE
        self._store.state_dir.mkdir(parents=True, exist_ok=True)
        with (self._store.state_dir / LOCK_FILE).open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
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
        with self._backend.keep_awake():
            result = self._run(
                self._run_command, self._settings.run_time_limit_minutes * 60, self._log_path()
            )
        if result.succeeded:
            self._log(f"run finished: {result.summary()}")
            self._backend.notify("Job hunt", f"Run finished: {result.summary()}")
            return CheckOutcome.RAN
        history = self._store.load_history()
        released = history.record_failure(due, self._settings.attempts_per_slot)
        self._store.save_history(history)
        retry = "retrying at the next check" if released else "no retries left for this slot"
        self._log(f"run failed (exit {result.exit_code}): {result.summary()}; {retry}")
        self._backend.notify("Job hunt", f"Run failed: {result.summary()}; {retry}. Log: {self._log_path()}")
        return CheckOutcome.FAILED

    def _wait_for_internet(self) -> bool:
        tries = max(1, self._settings.internet_wait_seconds // SECONDS_BETWEEN_CONNECTIVITY_TRIES)
        for attempt in range(tries):
            if self._online():
                return True
            if attempt < tries - 1:
                self._sleep(SECONDS_BETWEEN_CONNECTIVITY_TRIES)
        return False

    # once a day, when the booked wakes are about to run out
    def _remind_to_book_wakes(self, wake: bool, history: SlotHistory) -> None:
        now = self._clock()
        today = now.date().isoformat()
        if not wake or history.wake_reminder_day == today:
            return
        booked_until = self._backend.wakes_booked_until(now)
        if booked_until is not None and booked_until - now > timedelta(days=WAKE_REMINDER_DAYS):
            return
        history.wake_reminder_day = today
        self._store.save_history(history)
        self._backend.notify(
            "Job hunt", "Booked wakes run out soon: run `job-hunt schedule renew` in Terminal."
        )

    def _log_path(self) -> Path:
        self._settings.log_dir.mkdir(parents=True, exist_ok=True)
        return self._settings.log_dir / f"{self._clock():%Y-%m-%d}.log"

    def _log(self, message: str) -> None:
        logger.info("scheduler: %s", message)
        with self._log_path().open("a") as log:
            log.write(f"══ {self._clock():%Y-%m-%d %H:%M:%S} {message}\n")
