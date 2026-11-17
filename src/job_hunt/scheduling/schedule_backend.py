"""What the operating system does for the scheduler: start a check every few
minutes, keep the computer from sleeping during a run, and show a
notification. It never wakes the computer. One backend per system
(launchd, systemd, Task Scheduler); `auto` picks this system's."""

import shlex
import subprocess
import sys
from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from typing import ClassVar

from job_hunt.common.registry import Registry
from job_hunt.common.settings_schema import SchedulingSettings
from job_hunt.scheduling.schedule_store import ScheduleStore

schedule_backend_registry: Registry[type["ScheduleBackend"]] = Registry("schedule backend")
# sys.platform -> the backend `auto` picks there
BACKEND_FOR_PLATFORM = {"darwin": "launchd", "linux": "systemd", "win32": "task_scheduler"}


class ScheduleBackendError(RuntimeError):
    pass


class ScheduleBackend(ABC):
    name: ClassVar[str]

    def __init__(self, settings: SchedulingSettings, store: ScheduleStore):
        self._settings = settings
        self._store = store

    # start check_command every check_interval_minutes (and at login);
    # returns notes for the user
    @abstractmethod
    def install(self, check_command: list[str]) -> list[str]: ...

    @abstractmethod
    def uninstall(self) -> None: ...

    # lines for `job-hunt schedule status`
    @abstractmethod
    def describe(self) -> list[str]: ...

    # the computer doesn't go to sleep while the context is open
    @abstractmethod
    def keep_awake(self) -> AbstractContextManager[None]: ...

    @abstractmethod
    def notify(self, title: str, message: str) -> None: ...


# a system tool (launchctl, systemctl, schtasks); check=True raises if it fails
def run_system_command(command: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except FileNotFoundError as error:
        raise ScheduleBackendError(f"{command[0]} isn't available on this system") from error
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ScheduleBackendError(f"{shlex.join(command)} failed: {detail}")
    return result


def register_schedule_backend(backend_class: type[ScheduleBackend]) -> type[ScheduleBackend]:
    schedule_backend_registry.register(backend_class.name)(backend_class)
    return backend_class


def backend_name(settings: SchedulingSettings, platform: str = sys.platform) -> str:
    if settings.backend != "auto":
        return settings.backend
    if platform not in BACKEND_FOR_PLATFORM:
        raise ScheduleBackendError(f"no scheduler for {platform}; set scheduling.backend")
    return BACKEND_FOR_PLATFORM[platform]


def create_schedule_backend(settings: SchedulingSettings, store: ScheduleStore) -> ScheduleBackend:
    # importing registers the built-in backends
    from job_hunt.scheduling import launchd_backend, systemd_backend, task_scheduler_backend  # noqa: F401

    return schedule_backend_registry.get(backend_name(settings))(settings, store)
