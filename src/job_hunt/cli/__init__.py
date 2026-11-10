# importing the package registers every command
from job_hunt.cli import company_commands, job_commands, maintenance_commands, sponsor_commands  # noqa: F401
from job_hunt.cli.cli_command import cli_command_registry

__all__ = ["cli_command_registry"]
