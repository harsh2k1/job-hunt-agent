"""job-hunt: finds data science and AI openings and keeps track of them.

Run `job-hunt <command> --help` for a command's options. Also runs as
`python -m job_hunt <command>` without installing.
"""

import argparse
import sys

from job_hunt.cli import cli_command_registry


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="job-hunt", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="<command>")
    for command_name in cli_command_registry.names():
        command = cli_command_registry.get(command_name)()
        command_parser = subparsers.add_parser(
            command_name, help=command.summary, description=command.summary
        )
        command.add_arguments(command_parser)
        command_parser.set_defaults(command=command)
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    sys.exit(args.command.run(args))


if __name__ == "__main__":
    main()
