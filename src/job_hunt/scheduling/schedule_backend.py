"""What the operating system does for the scheduler: start a check every few
minutes, wake the machine for a slot, keep it awake during a run, and show
a notification. launchd is the macOS backend; another OS is one new class
registered here."""

from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from datetime import datetime
from typing import ClassVar

from job_hunt.common.registry import Registry
from job_hunt.common.settings_schema import SchedulingSettings
from job_hunt.scheduling.schedule_spec import ScheduleSpec
from job_hunt.scheduling.schedule_store import ScheduleStore

schedule_backend_registry: Registry[type["ScheduleBackend"]] = Registry("schedule backend")


class ScheduleBackendError(RuntimeError):
    pass


class ScheduleBackend(ABC):
    name: ClassVar[str]

    def __init__(self, settings: SchedulingSettings, store: ScheduleStore):
        self._settings = settings
        self._store = store

    # start running check_command every check_interval_minutes (and once
    # now) with path_dirs as its PATH, and book or cancel wakes as the spec
    # says; returns notes for the user
    @abstractmethod
    def install(
        self, check_command: list[str], path_dirs: list[str], spec: ScheduleSpec, now: datetime
    ) -> list[str]: ...

    # stop the checks and cancel any wakes booked
    @abstractmethod
    def uninstall(self) -> list[str]: ...

    # lines for `job-hunt schedule status`
    @abstractmethod
    def describe(self, now: datetime) -> list[str]: ...

    # the last booked wake, or None when none are booked
    @abstractmethod
    def wakes_booked_until(self, now: datetime) -> datetime | None: ...

    # the machine stays awake while the context is open
    @abstractmethod
    def keep_awake(self) -> AbstractContextManager[None]: ...

    @abstractmethod
    def notify(self, title: str, message: str) -> None: ...


def register_schedule_backend(backend_class: type[ScheduleBackend]) -> type[ScheduleBackend]:
    schedule_backend_registry.register(backend_class.name)(backend_class)
    return backend_class


def create_schedule_backend(settings: SchedulingSettings, store: ScheduleStore) -> ScheduleBackend:
    # importing registers the built-in backends
    from job_hunt.scheduling import launchd_backend  # noqa: F401

    return schedule_backend_registry.get(settings.backend)(settings, store)
