import argparse

from job_hunt.cli.cli_command import EXIT_FAILURE, EXIT_SUCCESS, CliCommand, register_cli_command
from job_hunt.common.http_client import HttpRequestError
from job_hunt.common.settings import settings
from job_hunt.company_discovery.board_search import BoardSearchDiscovery, BoardSearchError
from job_hunt.company_discovery.crawl_discovery import CrawlIndexDiscovery
from job_hunt.company_discovery.seed_list import load_seed_companies
from job_hunt.job_sources.ats_boards.ats_board_links import AtsBoardLink, find_ats_board
from job_hunt.job_store.company_repository import CompanyRepository
from job_hunt.job_store.models import AtsPlatform, DiscoveredCompany

ADDED_BY_HAND = "added by hand"


@register_cli_command
class AddCompanyCommand(CliCommand):
    name = "add-company"
    summary = "start reading a company's job board, from its URL or ATS + slug"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--name", required=True, help="company name as it should appear")
        board = parser.add_mutually_exclusive_group(required=True)
        board.add_argument(
            "--board-url",
            help="any link to the board or one of its jobs, e.g. https://jobs.ashbyhq.com/perplexity",
        )
        board.add_argument(
            "--ats", choices=[platform.value for platform in AtsPlatform], help="the ATS, with --slug"
        )
        parser.add_argument("--slug", help="the board's slug on that ATS, with --ats")

    def run(self, args: argparse.Namespace) -> int:
        board_link = self._board_from_arguments(args)
        if board_link is None:
            print("That URL isn't a board on any supported ATS (or --ats was given without --slug).")
            return EXIT_FAILURE
        company = DiscoveredCompany(
            name=args.name,
            ats_platform=board_link.ats_platform,
            ats_board_slug=board_link.ats_board_slug,
            discovered_via=ADDED_BY_HAND,
        )
        if CompanyRepository().add_company_if_new(company):
            print(f"Tracking {company.name} on {company.ats_platform.value} ({company.ats_board_slug}).")
        else:
            print(f"{company.ats_platform.value} board {company.ats_board_slug!r} is already on the list.")
        return EXIT_SUCCESS

    @staticmethod
    def _board_from_arguments(args: argparse.Namespace) -> AtsBoardLink | None:
        if args.board_url:
            return find_ats_board(args.board_url)
        if not args.slug:
            return None
        return AtsBoardLink(ats_platform=AtsPlatform(args.ats), ats_board_slug=args.slug.lower())


@register_cli_command
class SeedCompaniesCommand(CliCommand):
    name = "seed-companies"
    summary = "add every company in the seed list (safe to rerun; existing boards are skipped)"

    def run(self, args: argparse.Namespace) -> int:
        repository = CompanyRepository()
        seed_companies = load_seed_companies(settings.companies.seed_list_path)
        added = sum(repository.add_company_if_new(company) for company in seed_companies)
        print(f"Added {added} of {len(seed_companies)} seed companies; the rest were already tracked.")
        return EXIT_SUCCESS


WEB_SEARCH = "web-search"
COMMON_CRAWL = "common-crawl"


@register_cli_command
class DiscoverCompaniesCommand(CliCommand):
    name = "discover-companies"
    summary = "find company job boards (web search, Common Crawl) and start tracking them"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--via",
            choices=[WEB_SEARCH, COMMON_CRAWL],
            action="append",
            help="only this way of finding boards; repeat for both (the default)",
        )
        parser.add_argument("--dry-run", action="store_true", help="show new boards without tracking them")

    # Each way runs on its own, so a search engine that's blocking us doesn't
    # stop the crawl and the other way round. Exit 1 if either failed.
    def run(self, args: argparse.Namespace) -> int:
        ways = args.via or [WEB_SEARCH, COMMON_CRAWL]
        verb = "would be added" if args.dry_run else "added"
        succeeded = True
        if WEB_SEARCH in ways:
            succeeded &= self._run_web_search(args.dry_run, verb)
        if COMMON_CRAWL in ways:
            succeeded &= self._run_common_crawl(args.dry_run, verb)
        return EXIT_SUCCESS if succeeded else EXIT_FAILURE

    @staticmethod
    def _run_web_search(dry_run: bool, verb: str) -> bool:
        try:
            report = BoardSearchDiscovery.from_settings().run(dry_run=dry_run)
        except BoardSearchError as error:
            print(f"Web search: failed: {error}")
            return False
        print_new_companies(report.new_companies)
        print(
            f"Web search: {report.queries} queries, {report.cached_queries} from cache, "
            f"{report.searches_run} searched ({report.failed_queries} failed), "
            f"{report.deferred_queries} left for the next run. "
            f"{len(report.boards_found)} boards found, {len(report.new_companies)} new {verb}."
        )
        return True

    @staticmethod
    def _run_common_crawl(dry_run: bool, verb: str) -> bool:
        try:
            report = CrawlIndexDiscovery.from_settings().run(dry_run=dry_run)
        except HttpRequestError as error:
            print(f"Common Crawl: failed: {error}")
            return False
        print_new_companies(report.new_companies)
        print(
            f"Common Crawl ({', '.join(report.crawl_ids)}): {report.boards_in_crawls} boards, "
            f"{report.already_tracked} tracked already, {report.recently_checked} checked recently, "
            f"{report.boards_checked} checked now ({report.failed_checks} failed), {report.deferred_checks} "
            f"left for the next run. {len(report.new_companies)} with UK jobs {verb}."
        )
        return True


def print_new_companies(companies: list[DiscoveredCompany]) -> None:
    for company in companies:
        print(f"{company.name:<30} {company.ats_platform.value:<16} {company.ats_board_slug}")


@register_cli_command
class ListCompaniesCommand(CliCommand):
    name = "list-companies"
    summary = "companies whose job boards are read on every fetch"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--ats", choices=[platform.value for platform in AtsPlatform], help="only this ATS"
        )

    def run(self, args: argparse.Namespace) -> int:
        ats_platform = AtsPlatform(args.ats) if args.ats else None
        companies = CompanyRepository().list_active_companies(ats_platform)
        if not companies:
            print("No companies tracked yet.")
        for company in companies:
            platform = company.ats_platform.value
            print(f"{company.name:<30} {platform:<16} {company.ats_board_slug:<30} {company.discovered_via}")
        return EXIT_SUCCESS
