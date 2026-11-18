import argparse
import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

from job_hunt.cli.cli_command import EXIT_FAILURE, EXIT_SUCCESS, CliCommand, register_cli_command
from job_hunt.common.settings import settings
from job_hunt.scheduling.schedule_backend import (
    ScheduleBackend,
    ScheduleBackendError,
    create_schedule_backend,
)
from job_hunt.scheduling.schedule_spec import (
    FREQUENCIES,
    InvalidTimeError,
    ScheduleSpec,
    evenly_spaced,
    parse_clock_time,
)
from job_hunt.scheduling.schedule_store import ScheduleStore
from job_hunt.scheduling.scheduled_check import CheckOutcome, ScheduledCheck
from job_hunt.scheduling.slots import due_slots

# what a scheduled slot runs, and what the backend starts every few minutes,
# with this same Python (the project's venv)
RUN_COMMAND = [sys.executable, "-m", "job_hunt", "run"]
CHECK_COMMAND = [sys.executable, "-m", "job_hunt", "schedule", "check"]


@register_cli_command
class ScheduleCommand(CliCommand):
    name = "schedule"
    summary = "run the daily job hunt automatically at set times (set / status / off)"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        actions = parser.add_subparsers(dest="action", required=True, metavar="<action>")
        set_parser = actions.add_parser(
            "set",
            help="choose when it runs, and whether a run missed while the computer slept still happens",
            description="e.g. `job-hunt schedule set --at 3am --at 9pm` or "
            "`job-hunt schedule set --times-per-day 3 --first 03:00` (03:00, 11:00, 19:00).",
        )
        when = set_parser.add_mutually_exclusive_group(required=True)
        when.add_argument(
            "--at",
            action="append",
            metavar="TIME",
            help="a run time (03:00, 21:30, 3am, 9pm); repeat for more",
        )
        when.add_argument(
            "--times-per-day", type=int, metavar="N", help="N runs a day, evenly spaced from --first"
        )
        set_parser.add_argument(
            "--first",
            default="03:00",
            metavar="TIME",
            help="with --times-per-day: the first run (default 03:00)",
        )
        set_parser.add_argument(
            "--frequency",
            choices=FREQUENCIES,
            default="daily",
            help="how often the times repeat (default daily)",
        )
        # off unless asked for: a run missed while the computer slept is skipped.
        # The computer is never woken for a run.
        set_parser.add_argument(
            "--run-on-wake",
            action="store_true",
            help="a run missed while the computer slept or was off happens as soon as it's back "
            "(without this it's skipped)",
        )
        actions.add_parser("status", help="the schedule, what's due, and the last runs")
        actions.add_parser("off", help="stop the scheduled runs")
        actions.add_parser(
            "check", help="what the scheduler runs every few minutes: run a slot if one is due"
        )

    def run(self, args: argparse.Namespace) -> int:
        store = ScheduleStore(settings.scheduling.state_dir)
        try:
            backend = create_schedule_backend(settings.scheduling, store)
            if args.action == "set":
                return self._set(args, store, backend)
            if args.action == "off":
                return self._off(store, backend)
            if args.action == "status":
                return self._status(store, backend)
        except ScheduleBackendError as error:
            print(f"Couldn't set up the schedule: {error}")
            return EXIT_FAILURE
        outcome = _checker(store, backend).check()
        return EXIT_FAILURE if outcome is CheckOutcome.FAILED else EXIT_SUCCESS

    def _set(self, args: argparse.Namespace, store: ScheduleStore, backend: ScheduleBackend) -> int:
        try:
            times = (
                tuple(parse_clock_time(text) for text in args.at)
                if args.at
                else evenly_spaced(args.times_per_day, parse_clock_time(args.first))
            )
        except InvalidTimeError as error:
            print(error)
            return EXIT_FAILURE
        spec = ScheduleSpec(
            frequency=args.frequency, times=times, catch_up=args.run_on_wake, active_since=datetime.now()
        )
        store.save_spec(spec)
        store.save_run_path(_run_path())
        notes = backend.install(CHECK_COMMAND, spec.times)
        print(f"Scheduled: job-hunt run, {spec.describe()}.")
        print(_sleep_rules(spec))
        for note in notes:
            print(note)
        print("Check on it with `job-hunt schedule status`.")
        return EXIT_SUCCESS

    def _off(self, store: ScheduleStore, backend: ScheduleBackend) -> int:
        backend.uninstall()
        store.remove_spec()
        print("Scheduled runs are off.")
        return EXIT_SUCCESS

    def _status(self, store: ScheduleStore, backend: ScheduleBackend) -> int:
        spec = store.load_spec()
        if spec is None:
            print("Nothing scheduled. Set it with `job-hunt schedule set --at 03:00`.")
        else:
            now = datetime.now()
            print(f"Schedule: {spec.describe()} (set {spec.active_since:%Y-%m-%d %H:%M})")
            print(_sleep_rules(spec))
            history = store.load_history()
            missed = due_slots(spec, now, history, grace=_grace())
            if missed:
                print(
                    "Due now (runs at the next check): "
                    + ", ".join(f"{slot:%Y-%m-%d %H:%M}" for slot in missed)
                )
            last_run = store.load_last_run()
            running = _checker(store, backend).run_in_progress()
            print(_last_run_line(last_run, running, settings.scheduling.log_dir))
        for line in backend.describe():
            print(line)
        return EXIT_SUCCESS


def _checker(store: ScheduleStore, backend: ScheduleBackend) -> ScheduledCheck:
    return ScheduledCheck(settings.scheduling, store, backend, RUN_COMMAND)


# What the latest run did, or that one is going now (a run holds the lock),
# with the command that shows its log.
def _last_run_line(last_run: dict | None, running: bool, log_dir: Path) -> str:
    started = datetime.fromisoformat(last_run["started"]) if last_run else None
    unfinished = last_run is not None and last_run["finished"] is None
    log = _run_log(log_dir, started if unfinished or not running else None)
    if running:
        if started is not None and unfinished and last_run is not None:
            return (
                f"Running now: started {started:%Y-%m-%d %H:%M} for slot {last_run['slots']}. "
                f"Follow it: tail -f {log}"
            )
        return f"Running now. Follow it: tail -f {log}"
    if last_run is None or started is None:
        return "Last run: none yet"
    if unfinished:
        return (
            f"Last run: started {started:%Y-%m-%d %H:%M} for slot {last_run['slots']} and stopped "
            f"without finishing (the check was killed or the computer shut down). See: tail -n 50 {log}"
        )
    finished = datetime.fromisoformat(last_run["finished"])
    return (
        f"Last run: {last_run['outcome']}, {started:%Y-%m-%d %H:%M}-{finished:%H:%M} "
        f"(slot {last_run['slots']}): {last_run['summary']}. Log: tail -n 50 {log}"
    )


# A run writes to the log of the day it started; with no record of when,
# the newest day's log.
def _run_log(log_dir: Path, started: datetime | None) -> Path:
    if started is not None:
        return log_dir / f"{started:%Y-%m-%d}.log"
    days = sorted(log_dir.glob("20??-??-??.log"))
    return days[-1] if days else log_dir / f"{datetime.now():%Y-%m-%d}.log"


def _grace() -> timedelta:
    return timedelta(minutes=settings.scheduling.on_time_grace_minutes)


def _sleep_rules(spec: ScheduleSpec) -> str:
    missed = (
        "it runs once it's back" if spec.catch_up else "that run is skipped (--run-on-wake to run it later)"
    )
    return f"  If the computer is asleep or off at a run time, {missed}. The computer is never woken."


# what the run's PATH starts with: this Python's folder (job-hunt), the
# claude CLI's, then the PATH of the terminal the schedule was set from
def _run_path() -> list[str]:
    claude = shutil.which(settings.llm.claude_cli_binary)
    folders = [str(Path(sys.executable).parent), *([str(Path(claude).parent)] if claude else [])]
    return list(dict.fromkeys([*folders, *filter(None, os.environ.get("PATH", "").split(os.pathsep))]))
