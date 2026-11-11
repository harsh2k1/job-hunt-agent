import argparse
from datetime import timedelta

from job_hunt.cli.cli_command import EXIT_FAILURE, EXIT_SUCCESS, CliCommand, register_cli_command
from job_hunt.common.settings import settings
from job_hunt.job_pipeline.daily_run import DailyRun
from job_hunt.job_pipeline.job_fetch_run import JobFetchRun, SourceFetchReport
from job_hunt.job_pipeline.listings import ListingBuilder
from job_hunt.job_pipeline.source_health import HealthStatus, SourceHealthCheck
from job_hunt.job_sources.job_source_factory import JobSourceFactory
from job_hunt.job_sources.location_matching import TargetLocationMatcher
from job_hunt.job_store.job_repository import JobRepository
from job_hunt.job_store.models import utc_now
from job_hunt.sheet_sync.google_sheet import GoogleSheetSync, SheetSyncError


@register_cli_command
class FetchJobsCommand(CliCommand):
    name = "fetch"
    summary = "fetch jobs from every enabled source, or the ones named"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--source",
            dest="source_names",
            action="append",
            choices=JobSourceFactory.registered_source_names(),
            help="only this source; repeat for more (runs even if disabled in settings)",
        )

    # Exit code 1 if any source failed, so a scheduled run gets noticed.
    def run(self, args: argparse.Namespace) -> int:
        reports = JobFetchRun(JobSourceFactory.create_sources(args.source_names)).run()
        print_fetch_reports(reports)
        return EXIT_FAILURE if any(report.error for report in reports) else EXIT_SUCCESS


@register_cli_command
class ListSourcesCommand(CliCommand):
    name = "list-sources"
    summary = "every job source and whether it's enabled"

    def run(self, args: argparse.Namespace) -> int:
        for source_name in JobSourceFactory.registered_source_names():
            state = "enabled" if JobSourceFactory.is_enabled(source_name) else "disabled"
            print(f"{source_name:<18} {state}")
        return EXIT_SUCCESS


@register_cli_command
class JobCountsCommand(CliCommand):
    name = "job-counts"
    summary = "how many jobs each source has found so far"

    def run(self, args: argparse.Namespace) -> int:
        counts = JobRepository().count_jobs_by_source()
        if not counts:
            print("No jobs stored yet.")
        for source_name, count in sorted(counts.items()):
            print(f"{source_name:<18} {count}")
        return EXIT_SUCCESS


def print_fetch_reports(reports: list[SourceFetchReport]) -> None:
    print(f"\n{'source':<18} {'fetched':>8} {'new':>6} {'known':>6} {'companies+':>11}  error")
    for report in reports:
        print(
            f"{report.source_name:<18} {report.fetched_jobs:>8} {report.new_jobs:>6} "
            f"{report.already_known_jobs:>6} {report.companies_added:>11}  {report.error or ''}"
        )


@register_cli_command
class ListingsCommand(CliCommand):
    name = "listings"
    summary = "jobs still advertised, one per advert however many sources list it"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--days", type=int, default=7, help="seen by a source in the last N days (default 7)"
        )
        parser.add_argument("--show", type=int, default=10, help="print the N listings most sources agree on")

    def run(self, args: argparse.Namespace) -> int:
        jobs = JobRepository().list_jobs_seen_since(utc_now() - timedelta(days=args.days))
        builder = ListingBuilder(TargetLocationMatcher(settings.job_search.target_location_keywords))
        listings = builder.build(jobs)
        print(f"{len(jobs)} jobs seen in the last {args.days} days -> {len(listings)} listings.")
        for listing in sorted(listings, key=lambda listing: -len(listing.sources))[: args.show]:
            job = listing.primary
            print(f"  {job.title[:45]:<45} {job.company_name[:22]:<22} {', '.join(listing.sources)}")
        return EXIT_SUCCESS


@register_cli_command
class DailyRunCommand(CliCommand):
    name = "run"
    summary = "the daily run: sponsor register, company discovery, every enabled source, health check"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--no-discovery", action="store_true", help="skip finding new company boards")
        parser.add_argument(
            "--no-sponsor-register", action="store_true", help="skip the sponsor register check"
        )

    # Exit code 1 if any step failed, so a scheduled run gets noticed.
    def run(self, args: argparse.Namespace) -> int:
        outcomes = DailyRun.from_settings(
            include_discovery=not args.no_discovery, include_sponsor_register=not args.no_sponsor_register
        ).run()
        for outcome in outcomes:
            print(f"{'ok' if outcome.succeeded else 'FAILED':<7} {outcome.step:<24} {outcome.summary}")
        return EXIT_SUCCESS if all(outcome.succeeded for outcome in outcomes) else EXIT_FAILURE


@register_cli_command
class SourceHealthCommand(CliCommand):
    name = "source-health"
    summary = "each source's last run against its usual job count"

    def run(self, args: argparse.Namespace) -> int:
        results = SourceHealthCheck(settings.source_health).check_all()
        if not results:
            print("No runs recorded yet.")
        for health in results:
            print(f"{health.status.value:<8} {health.source:<22} {health.detail}")
        return (
            EXIT_FAILURE if any(health.status is HealthStatus.FAILING for health in results) else EXIT_SUCCESS
        )


@register_cli_command
class SyncSheetCommand(CliCommand):
    name = "sync-sheet"
    summary = "rewrite the Google Sheet from the current listings (your Status and Notes are kept)"

    def run(self, args: argparse.Namespace) -> int:
        try:
            kept, skipped = GoogleSheetSync.from_settings().sync()
        except SheetSyncError as error:
            print(f"Sheet sync failed: {error}")
            return EXIT_FAILURE
        print(f"Sheet updated: {kept} listings on Jobs, {skipped} on Skipped (with the reason).")
        return EXIT_SUCCESS
