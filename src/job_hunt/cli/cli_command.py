import argparse
from abc import ABC, abstractmethod
from typing import ClassVar, TypeVar

from job_hunt.common.registry import Registry

# what a command returns becomes the process exit code
EXIT_SUCCESS = 0
EXIT_FAILURE = 1

cli_command_registry: Registry[type["CliCommand"]] = Registry("command")


class CliCommand(ABC):
    """One `job-hunt <name>` subcommand. A new command is one subclass
    decorated with `register_cli_command`; main.py picks it up."""

    name: ClassVar[str]
    # one line, shown in `job-hunt --help`
    summary: ClassVar[str]

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        return None

    @abstractmethod
    def run(self, args: argparse.Namespace) -> int: ...


CliCommandClassT = TypeVar("CliCommandClassT", bound=type[CliCommand])


def register_cli_command(command_class: CliCommandClassT) -> CliCommandClassT:
    cli_command_registry.register(command_class.name)(command_class)
    return command_class
