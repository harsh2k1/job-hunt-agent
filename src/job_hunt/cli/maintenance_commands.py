import argparse

from job_hunt.cli.cli_command import EXIT_SUCCESS, CliCommand, register_cli_command
from job_hunt.common.cache import get_cache
from job_hunt.common.database.schema_migrations import upgrade_schema_to_latest
from job_hunt.common.logger import get_logger
from job_hunt.common.settings import settings

logger = get_logger(__name__)


@register_cli_command
class MigrateDatabaseCommand(CliCommand):
    name = "migrate-db"
    summary = "bring the database schema up to date (safe to rerun)"

    def run(self, args: argparse.Namespace) -> int:
        upgrade_schema_to_latest()
        logger.info("Database schema up to date (%s backend)", settings.database.backend)
        return EXIT_SUCCESS


@register_cli_command
class ShowSettingsCommand(CliCommand):
    name = "show-settings"
    summary = "print the settings this run would use (secrets masked)"

    def run(self, args: argparse.Namespace) -> int:
        print(settings.model_dump_json(indent=2))
        return EXIT_SUCCESS


@register_cli_command
class ClearCacheCommand(CliCommand):
    name = "clear-cache"
    summary = "drop every cached response"

    def run(self, args: argparse.Namespace) -> int:
        get_cache().clear()
        logger.info("Cache cleared (%s backend)", settings.cache.backend)
        return EXIT_SUCCESS
