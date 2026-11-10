import argparse

from job_hunt.cli.cli_command import EXIT_FAILURE, EXIT_SUCCESS, CliCommand, register_cli_command
from job_hunt.common.http_client import HttpRequestError, get_http_client
from job_hunt.job_store.sponsor_repository import SponsorRepository
from job_hunt.sponsor_register.register_file import (
    RegisterNotFoundError,
    find_current_register,
    read_register,
)


@register_cli_command
class UpdateSponsorRegisterCommand(CliCommand):
    name = "update-sponsor-register"
    summary = "load the latest Home Office register of licensed sponsors (skipped if already current)"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--force", action="store_true", help="reload even if this register is already loaded"
        )

    def run(self, args: argparse.Namespace) -> int:
        http_client = get_http_client()
        repository = SponsorRepository()
        try:
            register = find_current_register(http_client)
            if register.register_date == repository.register_date() and not args.force:
                print(f"The register of {register.register_date} is already loaded.")
                return EXIT_SUCCESS
            sponsors = read_register(http_client.get_text(register.url), register.register_date)
        except (HttpRequestError, RegisterNotFoundError) as error:
            print(f"Couldn't get the register: {error}")
            return EXIT_FAILURE
        loaded = repository.replace_register(sponsors)
        print(f"Loaded {loaded} entries from the register of {register.register_date}.")
        return EXIT_SUCCESS


@register_cli_command
class CheckSponsorCommand(CliCommand):
    name = "check-sponsor"
    summary = "is this employer on the register of licensed sponsors?"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("employer", help="the employer's name, e.g. Monzo or Deliveroo")

    def run(self, args: argparse.Namespace) -> int:
        repository = SponsorRepository()
        if repository.register_date() is None:
            print("No register loaded yet; run update-sponsor-register first.")
            return EXIT_FAILURE
        matches = repository.find_sponsors(args.employer)
        if not matches:
            print(
                f"{args.employer!r} isn't on the register under that name "
                "(it may hold a licence under its legal name)."
            )
        # one line per organisation and place, with every route it's licensed for
        routes_by_entry: dict[tuple[bool, str, str], list[str]] = {}
        for match in matches:
            entry = (match.is_exact, match.sponsor.organisation_name, match.sponsor.town_city or "")
            routes_by_entry.setdefault(entry, []).append(match.sponsor.route)
        for (is_exact, organisation, town), routes in routes_by_entry.items():
            kind = "exact" if is_exact else "possible"
            print(f"{kind:<9} {organisation:<50} {town:<18} {'; '.join(dict.fromkeys(routes))}")
        return EXIT_SUCCESS
