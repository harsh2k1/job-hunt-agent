"""macOS: launchd starts a check every few minutes, through a small app.

Why the app: a background job launched by launchd can't read ~/Documents
(where the project lives) until macOS grants access, and it only grants
that to an app. The app runs one line, the check command; macOS asks once
("JobHuntScheduler would like to access your Documents folder").

Why a periodic check rather than launchd's calendar timer: the calendar
timer proved unreliable on this Mac, and a check also covers catch-up
after sleep for free: the first check after a wake runs any missed slot.

Wakes use pmset, which repeats only one wake time a day, so each slot's
wake is booked as a one-off for the days ahead (one admin password prompt).
"""

import os
import plistlib
import shlex
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from job_hunt.common.logger import get_logger
from job_hunt.scheduling.schedule_backend import (
    ScheduleBackend,
    ScheduleBackendError,
    register_schedule_backend,
)
from job_hunt.scheduling.schedule_spec import ScheduleSpec

logger = get_logger(__name__)

LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
APP_SOURCE_FILE = "scheduler_app.applescript"
WAKES_FILE = "wakes.json"
PMSET_TIME_FORMAT = "%m/%d/%y %H:%M:%S"
# pmset tags each wake with its owner, so ours can be told apart
WAKE_OWNER = "job-hunt"
# launchd starts jobs with a bare PATH: these are added after the given folders
SYSTEM_PATH = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]


def app_source(check_command: list[str], path_dirs: list[str]) -> str:
    shell = f"export PATH={shlex.quote(':'.join(path_dirs))}; {shlex.join(check_command)} >/dev/null 2>&1"
    escaped = shell.replace("\\", "\\\\").replace('"', '\\"')
    return f'do shell script "{escaped}"\n'


def launch_agent_plist(label: str, app_path: Path, interval_minutes: int, log_dir: Path) -> bytes:
    return plistlib.dumps(
        {
            "Label": label,
            "ProgramArguments": [str(app_path / "Contents" / "MacOS" / "applet")],
            "StartInterval": interval_minutes * 60,
            # a check at load and at login too: a slot missed while off runs then
            "RunAtLoad": True,
            "StandardOutPath": str(log_dir / "launchd.out.log"),
            "StandardErrorPath": str(log_dir / "launchd.err.log"),
        }
    )


# every wake for the days ahead: minutes_before each slot, future ones only
def wake_times(spec: ScheduleSpec, now: datetime, days_ahead: int, minutes_before: int) -> list[datetime]:
    wakes = []
    for offset in range(days_ahead + 1):
        day = now.date() + timedelta(days=offset)
        for moment in spec.times:
            wake = datetime.combine(day, moment) - timedelta(minutes=minutes_before)
            if wake > now:
                wakes.append(wake)
    return sorted(wakes)


def pmset_commands(action: str, wakes: list[datetime]) -> list[str]:
    prefix = "pmset schedule cancel wake" if action == "cancel" else "pmset schedule wake"
    return [f"{prefix} {shlex.quote(wake.strftime(PMSET_TIME_FORMAT))} {WAKE_OWNER}" for wake in wakes]


@register_schedule_backend
class LaunchdBackend(ScheduleBackend):
    name = "launchd"

    @property
    def _label(self) -> str:
        return self._settings.launchd.label

    @property
    def _plist_path(self) -> Path:
        return LAUNCH_AGENTS_DIR / f"{self._label}.plist"

    @property
    def _domain(self) -> str:
        return f"gui/{os.getuid()}"

    def install(
        self, check_command: list[str], path_dirs: list[str], spec: ScheduleSpec, now: datetime
    ) -> list[str]:
        self._settings.log_dir.mkdir(parents=True, exist_ok=True)
        notes = self._build_app(check_command, path_dirs)
        LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        self._plist_path.write_bytes(
            launch_agent_plist(
                self._label,
                self._settings.launchd.app_path,
                self._settings.check_interval_minutes,
                self._settings.log_dir,
            )
        )
        # reloaded, so a changed interval or app takes effect; not loaded yet is fine
        _run(["launchctl", "bootout", f"{self._domain}/{self._label}"], check=False)
        _run(["launchctl", "bootstrap", self._domain, str(self._plist_path)])
        notes += self._book_wakes(spec, now) if spec.wake else self._cancel_wakes(now)
        return notes

    def uninstall(self) -> list[str]:
        _run(["launchctl", "bootout", f"{self._domain}/{self._label}"], check=False)
        self._plist_path.unlink(missing_ok=True)
        return self._cancel_wakes(datetime.now())

    def describe(self, now: datetime) -> list[str]:
        loaded = _run(["launchctl", "print", f"{self._domain}/{self._label}"], check=False)
        if loaded.returncode != 0:
            return [f"launchd job {self._label}: not loaded (no checks are running)"]
        details = {
            key.strip(): value.strip()
            for key, _, value in (line.partition("=") for line in loaded.stdout.splitlines())
            if key.strip() in ("state", "runs", "last exit code")
        }
        lines = [
            f"launchd job {self._label}: loaded, checks every {self._settings.check_interval_minutes} min "
            f"({details.get('runs', '?')} checks so far, last exit {details.get('last exit code', '?')})"
        ]
        booked_until = self.wakes_booked_until(now)
        if booked_until:
            lines.append(f"wakes booked until {booked_until:%Y-%m-%d %H:%M}")
        return lines

    def wakes_booked_until(self, now: datetime) -> datetime | None:
        future = [wake for wake in self._booked_wakes() if wake > now]
        return max(future) if future else None

    @contextmanager
    def keep_awake(self) -> Iterator[None]:
        # -i: no idle sleep; -s: no sleep at all while on the charger (lid closed included)
        caffeinate = subprocess.Popen(["caffeinate", "-i", "-s", "-w", str(os.getpid())])
        try:
            yield
        finally:
            caffeinate.terminate()

    def notify(self, title: str, message: str) -> None:
        script = (
            f"display notification {_applescript_string(message)} with title {_applescript_string(title)}"
        )
        _run(["osascript", "-e", script], check=False)

    # Rebuilt only when the command it runs changes: macOS ties the
    # Documents permission to the app, and a rebuilt app may be asked again.
    def _build_app(self, check_command: list[str], path_dirs: list[str]) -> list[str]:
        source = app_source(check_command, list(dict.fromkeys([*path_dirs, *SYSTEM_PATH])))
        source_path = self._store.state_dir / APP_SOURCE_FILE
        app_path = self._settings.launchd.app_path
        if app_path.exists() and source_path.exists() and source_path.read_text() == source:
            return []
        self._store.state_dir.mkdir(parents=True, exist_ok=True)
        source_path.write_text(source)
        app_path.parent.mkdir(parents=True, exist_ok=True)
        if app_path.exists():
            shutil.rmtree(app_path)
        _run(["osacompile", "-o", str(app_path), str(source_path)])
        return [
            f"Built {app_path.name}. macOS may ask once to let it access your Documents folder: "
            "click Allow, or the checks can't read the project."
        ]

    def _book_wakes(self, spec: ScheduleSpec, now: datetime) -> list[str]:
        self._cancel_wakes(now)
        wakes = wake_times(spec, now, self._settings.wake_days_ahead, self._settings.wake_minutes_before)
        print("Booking wakes needs your Mac admin password (asked once):")
        if not _sudo_shell(pmset_commands("book", wakes)):
            return ["Wakes NOT booked (no admin rights given); runs happen while the Mac is awake."]
        self._store.write_json(WAKES_FILE, [wake.isoformat() for wake in wakes])
        return [
            f"Booked {len(wakes)} wakes, {self._settings.wake_minutes_before} min before each slot, until "
            f"{wakes[-1]:%Y-%m-%d}; run `job-hunt schedule renew` before then to book more. A Mac only "
            "wakes on the charger with the lid closed; on battery with the lid shut it stays asleep."
        ]

    def _cancel_wakes(self, now: datetime) -> list[str]:
        future = [wake for wake in self._booked_wakes() if wake > now]
        if not future:
            return []
        print("Cancelling booked wakes needs your Mac admin password:")
        if not _sudo_shell(pmset_commands("cancel", future)):
            return [f"{len(future)} booked wakes could NOT be cancelled; see `pmset -g sched`."]
        self._store.write_json(WAKES_FILE, [])
        return [f"Cancelled {len(future)} booked wakes."]

    def _booked_wakes(self) -> list[datetime]:
        return [datetime.fromisoformat(text) for text in self._store.read_json(WAKES_FILE) or []]


def _run(command: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise ScheduleBackendError(
            f"{shlex.join(command)} failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result


# All commands in one sudo call, so the password is asked once (in the
# terminal). Each runs even if one before it fails (a wake already past
# can't be cancelled); False when sudo is refused or the last one fails.
def _sudo_shell(commands: list[str]) -> bool:
    if not commands:
        return True
    return subprocess.run(["sudo", "/bin/sh", "-c", "; ".join(commands)]).returncode == 0


def _applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
