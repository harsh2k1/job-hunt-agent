import argparse

from job_hunt.cli.cli_command import EXIT_FAILURE, EXIT_SUCCESS, CliCommand, register_cli_command
from job_hunt.common.http_client import HttpRequestError, get_http_client
from job_hunt.job_store.sponsor_repository import SponsorRepository
from job_hunt.sponsor_register.register_file import RegisterNotFoundError
from job_hunt.sponsor_register.register_update import update_sponsor_register


@register_cli_command
class UpdateSponsorRegisterCommand(CliCommand):
    name = "update-sponsor-register"
    summary = "load the latest Home Office register of licensed sponsors (skipped if already current)"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--force", action="store_true", help="reload even if this register is already loaded"
        )

    def run(self, args: argparse.Namespace) -> int:
        try:
            update = update_sponsor_register(get_http_client(), SponsorRepository(), force=args.force)
        except (HttpRequestError, RegisterNotFoundError) as error:
            print(f"Couldn't get the register: {error}")
            return EXIT_FAILURE
        if update.entries_loaded is None:
            print(f"The register of {update.register_date} is already loaded.")
        else:
            print(f"Loaded {update.entries_loaded} entries from the register of {update.register_date}.")
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
