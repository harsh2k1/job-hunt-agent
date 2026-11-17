# importing the package registers every command
from job_hunt.cli import (  # noqa: F401
    company_commands,
    job_commands,
    maintenance_commands,
    schedule_commands,
    sponsor_commands,
)
from job_hunt.cli.cli_command import cli_command_registry

__all__ = ["cli_command_registry"]
