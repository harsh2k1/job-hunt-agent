"""Windows: a Task Scheduler task starts the check every few minutes.

The task repeats every N minutes from a fixed start, and at logon, with
"start when available" on: a check missed while the PC slept or was off
runs as soon as it's back, which is what --run-on-wake builds on. "Wake to
run" is off: the task never wakes the PC. It runs pythonw.exe, so no
console window flashes up every few minutes.
"""

import ctypes
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from xml.sax.saxutils import escape

from job_hunt.scheduling.schedule_backend import (
    ScheduleBackend,
    register_schedule_backend,
    run_system_command,
)

TASK_FILE = "scheduler_task.xml"
# SetThreadExecutionState flags: keep the system (not the display) on until reset
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
CREATE_NO_WINDOW = 0x08000000


# the windowless Python next to the given one, when there is one
def windowless(check_command: list[str]) -> list[str]:
    python = Path(check_command[0])
    pythonw = python.with_name("pythonw.exe")
    return [
        str(pythonw) if python.name.lower() == "python.exe" and pythonw.exists() else str(python),
        *check_command[1:],
    ]


def task_xml(check_command: list[str], interval_minutes: int) -> str:
    command, *arguments = check_command
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>job-hunt: run a scheduled slot if one is due</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
    <TimeTrigger>
      <StartBoundary>2026-01-01T00:00:00</StartBoundary>
      <Repetition>
        <Interval>PT{interval_minutes}M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Hidden>true</Hidden>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(command)}</Command>
      <Arguments>{escape(subprocess.list2cmdline(arguments))}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def _powershell_string(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


@register_schedule_backend
class TaskSchedulerBackend(ScheduleBackend):
    name = "task_scheduler"

    @property
    def _task(self) -> str:
        return f"{self._settings.task_scheduler.folder}\\Check"

    def install(self, check_command: list[str]) -> list[str]:
        self._settings.log_dir.mkdir(parents=True, exist_ok=True)
        self._store.state_dir.mkdir(parents=True, exist_ok=True)
        task_path = self._store.state_dir / TASK_FILE
        # Task Scheduler reads task files as UTF-16
        task_path.write_text(
            task_xml(windowless(check_command), self._settings.check_interval_minutes), "utf-16"
        )
        run_system_command(["schtasks", "/Create", "/TN", self._task, "/XML", str(task_path), "/F"])
        return []

    def uninstall(self) -> None:
        run_system_command(["schtasks", "/Delete", "/TN", self._task, "/F"], check=False)

    def describe(self) -> list[str]:
        shown = run_system_command(
            ["schtasks", "/Query", "/TN", self._task, "/FO", "LIST", "/V"], check=False
        )
        if shown.returncode != 0:
            return [f"Task Scheduler task {self._task}: not registered (no checks are running)"]
        details = {
            key.strip(): value.strip()
            for key, _, value in (line.partition(":") for line in shown.stdout.splitlines())
        }
        return [
            f"Task Scheduler task {self._task}: {details.get('Status', '?')}, checks every "
            f"{self._settings.check_interval_minutes} min (last result {details.get('Last Result', '?')}, "
            f"next {details.get('Next Run Time', '?')})"
        ]

    @contextmanager
    def keep_awake(self) -> Iterator[None]:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        try:
            yield
        finally:
            kernel32.SetThreadExecutionState(ES_CONTINUOUS)

    def notify(self, title: str, message: str) -> None:
        balloon = f"{_powershell_string(title)}, {_powershell_string(message)}"
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$icon = New-Object System.Windows.Forms.NotifyIcon; "
            "$icon.Icon = [System.Drawing.SystemIcons]::Information; $icon.Visible = $true; "
            f"$icon.ShowBalloonTip(10000, {balloon}, 'Info'); "
            "Start-Sleep -Seconds 10; $icon.Dispose()"
        )
        # not waited for: the balloon stays up for 10 s
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            creationflags=CREATE_NO_WINDOW,
        )
