import argparse
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
    summary = "run the daily job hunt automatically at set times (set / status / renew / off)"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        actions = parser.add_subparsers(dest="action", required=True, metavar="<action>")
        set_parser = actions.add_parser(
            "set",
            help="choose when it runs, and what happens if the Mac is asleep then",
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
        # both off unless asked for: a run missed while the Mac slept is skipped
        set_parser.add_argument(
            "--run-on-wake",
            action="store_true",
            help="a run missed while the Mac slept or was off happens as soon as it's back "
            "(without this it's skipped)",
        )
        set_parser.add_argument(
            "--wake-mac",
            action="store_true",
            help="wake the Mac from sleep for each run; needs your admin password once, and the charger "
            "if the lid is closed",
        )
        actions.add_parser("status", help="the schedule, what's next, and the last runs")
        actions.add_parser("renew", help="set up again from the saved schedule (books more wakes)")
        actions.add_parser("off", help="stop the scheduled runs and cancel booked wakes")
        actions.add_parser(
            "check", help="what the scheduler runs every few minutes: run a slot if one is due"
        )

    def run(self, args: argparse.Namespace) -> int:
        store = ScheduleStore(settings.scheduling.state_dir)
        backend = create_schedule_backend(settings.scheduling, store)
        try:
            if args.action == "set":
                return self._set(args, store, backend)
            if args.action == "renew":
                return self._renew(store, backend)
            if args.action == "off":
                return self._off(store, backend)
            if args.action == "status":
                return self._status(store, backend)
        except ScheduleBackendError as error:
            print(f"Couldn't set up the schedule: {error}")
            return EXIT_FAILURE
        outcome = ScheduledCheck(settings.scheduling, store, backend, RUN_COMMAND).check()
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
            frequency=args.frequency,
            times=times,
            catch_up=args.run_on_wake,
            wake=args.wake_mac,
            active_since=datetime.now(),
        )
        store.save_spec(spec)
        return self._install(spec, backend)

    def _renew(self, store: ScheduleStore, backend: ScheduleBackend) -> int:
        spec = store.load_spec()
        if spec is None:
            print("Nothing scheduled yet: use `job-hunt schedule set`.")
            return EXIT_FAILURE
        return self._install(spec, backend)

    def _install(self, spec: ScheduleSpec, backend: ScheduleBackend) -> int:
        notes = backend.install(CHECK_COMMAND, _tool_dirs(), spec, datetime.now())
        print(f"Scheduled: job-hunt run, {spec.describe()}.")
        print(_sleep_rules(spec))
        for note in notes:
            print(note)
        print(f"Logs: {settings.scheduling.log_dir}/<date>.log. See it with `job-hunt schedule status`.")
        return EXIT_SUCCESS

    def _off(self, store: ScheduleStore, backend: ScheduleBackend) -> int:
        notes = backend.uninstall()
        store.remove_spec()
        print("Scheduled runs are off.")
        for note in notes:
            print(note)
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
            recent = sorted(history.claimed)[-5:]
            print("Recent slots run: " + (", ".join(recent) if recent else "none yet"))
        for line in backend.describe(datetime.now()):
            print(line)
        print(f"Logs: {settings.scheduling.log_dir}")
        return EXIT_SUCCESS


def _grace() -> timedelta:
    return timedelta(minutes=settings.scheduling.on_time_grace_minutes)


def _sleep_rules(spec: ScheduleSpec) -> str:
    catch_up = (
        "a run missed while the Mac slept or was off happens as soon as it's back"
        if spec.catch_up
        else "a run missed while the Mac slept or was off is skipped"
    )
    wake = "the Mac is woken for each run" if spec.wake else "the Mac isn't woken for runs"
    return f"  {catch_up}; {wake}."


# folders the run's tools live in: this Python's (job-hunt) and the claude CLI's
def _tool_dirs() -> list[str]:
    claude = shutil.which(settings.llm.claude_cli_binary)
    return [str(Path(sys.executable).parent)] + ([str(Path(claude).parent)] if claude else [])
