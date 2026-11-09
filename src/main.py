"""job-hunt: finds data science and AI openings and keeps track of them.

    job-hunt migrate-db      bring the database schema up to date (safe to rerun)
    job-hunt show-settings   print the settings this run would use
    job-hunt job-counts      how many jobs each source has found so far
    job-hunt clear-cache     drop every cached response

Also runs as `python src/main.py <command>` without installing.
"""

import argparse
from collections.abc import Callable

from common.cache import get_cache
from common.database.schema_migrations import upgrade_schema_to_latest
from common.logger import get_logger
from common.settings import settings
from job_store.job_repository import JobRepository

logger = get_logger(__name__)


def run_migrate_db(args: argparse.Namespace) -> None:
    upgrade_schema_to_latest()
    logger.info("Database schema up to date (%s backend)", settings.database.backend)


def run_show_settings(args: argparse.Namespace) -> None:
    # SecretStr fields print as '**********'
    print(settings.model_dump_json(indent=2))


def run_job_counts(args: argparse.Namespace) -> None:
    counts = JobRepository().count_jobs_by_source()
    if not counts:
        print("No jobs stored yet.")
        return
    for source, count in sorted(counts.items()):
        print(f"{source:<20} {count}")


def run_clear_cache(args: argparse.Namespace) -> None:
    get_cache().clear()
    logger.info("Cache cleared (%s backend)", settings.cache.backend)


# subcommand -> (handler, one-line help)
COMMANDS: dict[str, tuple[Callable[[argparse.Namespace], None], str]] = {
    "migrate-db": (run_migrate_db, "bring the database schema up to date (safe to rerun)"),
    "show-settings": (run_show_settings, "print the settings this run would use"),
    "job-counts": (run_job_counts, "how many jobs each source has found so far"),
    "clear-cache": (run_clear_cache, "drop every cached response"),
}


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="job-hunt", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, (_, help_text) in COMMANDS.items():
        subparsers.add_parser(name, help=help_text)
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    command_handler, _ = COMMANDS[args.command]
    command_handler(args)


if __name__ == "__main__":
    main()
