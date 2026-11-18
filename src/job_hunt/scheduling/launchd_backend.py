"""macOS: launchd starts a check every few minutes, through a small app.

Why the app: a background job launched by launchd can't read ~/Documents
(where the project lives) until macOS grants access, and it only grants
that to an app. The app runs one line, the check command; macOS asks once
("JobHuntScheduler would like to access your Documents folder").

Each run time has its own calendar trigger, so a run starts on time; a
check every few minutes backs it up, since launchd's calendar timer proved
unreliable on this Mac. A check that falls due while the Mac sleeps runs
when it wakes, which is what --run-on-wake builds on.
"""

import os
import plistlib
import shlex
import shutil
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import time as clock_time
from pathlib import Path

from job_hunt.scheduling.schedule_backend import (
    ScheduleBackend,
    register_schedule_backend,
    run_system_command,
)

LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
APP_SOURCE_FILE = "scheduler_app.applescript"
UNLOAD_WAIT_SECONDS = 10


# A failed shell command would make the app show an error dialog and wait
# for a click, and launchd starts no new check while it waits; a check's
# outcome is in its log and a notification, so the app ignores it.
def app_source(check_command: list[str]) -> str:
    shell = f"{shlex.join(check_command)} >/dev/null 2>&1"
    return f"try\n\tdo shell script {applescript_string(shell)}\nend try\n"


def launch_agent_plist(
    label: str, app_path: Path, interval_minutes: int, log_dir: Path, times: tuple[clock_time, ...]
) -> bytes:
    return plistlib.dumps(
        {
            "Label": label,
            "ProgramArguments": [str(app_path / "Contents" / "MacOS" / "applet")],
            # a check at each run time, so a run starts on time ...
            "StartCalendarInterval": [{"Hour": moment.hour, "Minute": moment.minute} for moment in times],
            # ... and every few minutes, in case a timed start doesn't fire
            "StartInterval": interval_minutes * 60,
            # a check at load and at login too: a slot missed while off can run then
            "RunAtLoad": True,
            "StandardOutPath": str(log_dir / "launchd.out.log"),
            "StandardErrorPath": str(log_dir / "launchd.err.log"),
        }
    )


def applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


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
    def _service(self) -> str:
        return f"gui/{os.getuid()}/{self._label}"

    def install(self, check_command: list[str], times: tuple[clock_time, ...]) -> list[str]:
        self._settings.log_dir.mkdir(parents=True, exist_ok=True)
        notes = self._build_app(check_command)
        LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        self._plist_path.write_bytes(
            launch_agent_plist(
                self._label,
                self._settings.launchd.app_path,
                self._settings.check_interval_minutes,
                self._settings.log_dir,
                times,
            )
        )
        # reloaded, so changed times or a new app take effect; not loaded yet is fine
        self._unload()
        run_system_command(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(self._plist_path)])
        return notes

    def uninstall(self) -> None:
        self._unload()
        self._plist_path.unlink(missing_ok=True)

    # bootout returns before launchd has let go of the job, and loading it
    # again before then fails ("Bootstrap failed: 5: Input/output error")
    def _unload(self) -> None:
        run_system_command(["launchctl", "bootout", self._service], check=False)
        deadline = time.monotonic() + UNLOAD_WAIT_SECONDS
        while time.monotonic() < deadline:
            if run_system_command(["launchctl", "print", self._service], check=False).returncode != 0:
                return
            time.sleep(0.2)

    def describe(self) -> list[str]:
        loaded = run_system_command(["launchctl", "print", self._service], check=False)
        if loaded.returncode != 0:
            return [f"launchd job {self._label}: not loaded (no checks are running)"]
        details = {
            key.strip(): value.strip()
            for key, _, value in (line.partition("=") for line in loaded.stdout.splitlines())
            if key.strip() in ("runs", "last exit code")
        }
        return [
            f"launchd job {self._label}: loaded, checks every {self._settings.check_interval_minutes} min "
            f"({details.get('runs', '?')} checks so far, last exit {details.get('last exit code', '?')})"
        ]

    @contextmanager
    def keep_awake(self) -> Iterator[None]:
        # -i: no idle sleep; -s: no sleep at all while on the charger
        caffeinate = subprocess.Popen(["caffeinate", "-i", "-s", "-w", str(os.getpid())])
        try:
            yield
        finally:
            caffeinate.terminate()

    def notify(self, title: str, message: str) -> None:
        script = f"display notification {applescript_string(message)} with title {applescript_string(title)}"
        run_system_command(["osascript", "-e", script], check=False)

    # Rebuilt only when the command it runs changes: macOS ties the
    # Documents permission to the app, and a rebuilt app may be asked again.
    def _build_app(self, check_command: list[str]) -> list[str]:
        source = app_source(check_command)
        source_path = self._store.state_dir / APP_SOURCE_FILE
        app_path = self._settings.launchd.app_path
        if app_path.exists() and source_path.exists() and source_path.read_text() == source:
            return []
        self._store.state_dir.mkdir(parents=True, exist_ok=True)
        source_path.write_text(source)
        app_path.parent.mkdir(parents=True, exist_ok=True)
        if app_path.exists():
            shutil.rmtree(app_path)
        run_system_command(["osacompile", "-o", str(app_path), str(source_path)])
        return [
            f"Built {app_path}. macOS may ask once to let it access your Documents folder: "
            "click Allow, or the checks can't read the project."
        ]
