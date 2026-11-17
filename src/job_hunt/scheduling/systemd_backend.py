"""Linux: a systemd user timer starts the check every few minutes.

The timer is a calendar timer (every N minutes past the hour) with
Persistent=true: a check that fell due while the machine was suspended or
off runs as soon as it's back, which is what --run-on-wake builds on.
User timers only run while the user is logged in, unless lingering is on
(`loginctl enable-linger`); install says so when it's off.
"""

import getpass
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from job_hunt.scheduling.schedule_backend import (
    ScheduleBackend,
    register_schedule_backend,
    run_system_command,
)

USER_UNIT_DIR = Path.home() / ".config" / "systemd" / "user"


# systemd's own quoting: each argument in double quotes, \ and " escaped, % doubled
def systemd_command_line(command: list[str]) -> str:
    return " ".join(
        '"' + part.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"' for part in command
    )


def service_unit(check_command: list[str], log_dir: Path) -> str:
    # a path, not a command line: only % is special
    log = str(log_dir / "systemd.log").replace("%", "%%")
    return (
        "[Unit]\n"
        "Description=job-hunt: run a scheduled slot if one is due\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={systemd_command_line(check_command)}\n"
        f"StandardOutput=append:{log}\n"
        f"StandardError=append:{log}\n"
    )


def timer_unit(interval_minutes: int) -> str:
    return (
        "[Unit]\n"
        "Description=job-hunt: look for a due slot every few minutes\n\n"
        "[Timer]\n"
        f"OnCalendar=*:0/{interval_minutes}\n"
        "# a check missed while suspended or off runs as soon as the machine is back\n"
        "Persistent=true\n"
        "AccuracySec=30s\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


@register_schedule_backend
class SystemdBackend(ScheduleBackend):
    name = "systemd"

    @property
    def _unit(self) -> str:
        return self._settings.systemd.unit_name

    def install(self, check_command: list[str]) -> list[str]:
        self._settings.log_dir.mkdir(parents=True, exist_ok=True)
        USER_UNIT_DIR.mkdir(parents=True, exist_ok=True)
        (USER_UNIT_DIR / f"{self._unit}.service").write_text(
            service_unit(check_command, self._settings.log_dir)
        )
        (USER_UNIT_DIR / f"{self._unit}.timer").write_text(timer_unit(self._settings.check_interval_minutes))
        run_system_command(["systemctl", "--user", "daemon-reload"])
        run_system_command(["systemctl", "--user", "enable", "--now", f"{self._unit}.timer"])
        linger = run_system_command(["loginctl", "show-user", getpass.getuser(), "-p", "Linger"], check=False)
        if "Linger=yes" in linger.stdout:
            return []
        return [
            "Checks run only while you're logged in; `loginctl enable-linger` lets them run when you aren't."
        ]

    def uninstall(self) -> None:
        run_system_command(["systemctl", "--user", "disable", "--now", f"{self._unit}.timer"], check=False)
        for suffix in (".timer", ".service"):
            (USER_UNIT_DIR / f"{self._unit}{suffix}").unlink(missing_ok=True)
        run_system_command(["systemctl", "--user", "daemon-reload"], check=False)

    def describe(self) -> list[str]:
        shown = run_system_command(
            [
                "systemctl",
                "--user",
                "show",
                f"{self._unit}.timer",
                "-p",
                "ActiveState",
                "-p",
                "NextElapseUSecRealtime",
            ],
            check=False,
        )
        details = dict(line.partition("=")[::2] for line in shown.stdout.splitlines())
        if details.get("ActiveState") != "active":
            return [f"systemd timer {self._unit}.timer: not active (no checks are running)"]
        interval = self._settings.check_interval_minutes
        return [
            f"systemd timer {self._unit}.timer: active, checks every {interval} min "
            f"(next {details.get('NextElapseUSecRealtime', '?')})"
        ]

    @contextmanager
    def keep_awake(self) -> Iterator[None]:
        if not shutil.which("systemd-inhibit"):
            yield
            return
        inhibitor = subprocess.Popen(
            ["systemd-inhibit", "--what=sleep:idle", "--who=job-hunt", "--why=scheduled run", "--mode=block",
             "sleep", "infinity"]
        )  # fmt: skip
        try:
            yield
        finally:
            inhibitor.terminate()

    def notify(self, title: str, message: str) -> None:
        if shutil.which("notify-send"):
            run_system_command(["notify-send", title, message], check=False)
