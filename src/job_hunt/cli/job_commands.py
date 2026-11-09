import argparse

from job_hunt.cli.cli_command import EXIT_FAILURE, EXIT_SUCCESS, CliCommand, register_cli_command
from job_hunt.job_pipeline.job_fetch_run import JobFetchRun, SourceFetchReport
from job_hunt.job_sources.job_source_factory import JobSourceFactory
from job_hunt.job_store.job_repository import JobRepository


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
