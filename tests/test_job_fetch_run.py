from collections.abc import Iterable

from job_hunt.common.settings_schema import Settings
from job_hunt.job_pipeline.job_fetch_run import JobFetchRun
from job_hunt.job_sources.job_source import JobSource
from job_hunt.job_store.company_repository import CompanyRepository
from job_hunt.job_store.job_repository import JobRepository
from job_hunt.job_store.models import AtsPlatform, FetchedJob
from job_hunt.job_store.source_run_repository import SourceRunRepository


def make_job(source: str, job_id: str, employer_url: str | None = None, company: str = "Acme") -> FetchedJob:
    return FetchedJob(
        source=source,
        source_job_id=job_id,
        title="Graduate Data Scientist",
        company_name=company,
        url=f"https://example.com/{job_id}",
        employer_url=employer_url,
    )


class StaticSource(JobSource[FetchedJob]):
    """A source that returns a fixed list, or raises."""

    def __init__(self, name: str, jobs: list[FetchedJob], error: Exception | None = None):
        self.name = name
        self._jobs = jobs
        self._error = error

    @classmethod
    def from_settings(cls, settings: Settings) -> "StaticSource":
        raise NotImplementedError

    def _list_raw_postings(self) -> Iterable[FetchedJob]:
        if self._error:
            raise self._error
        return self._jobs

    def _to_fetched_job(self, raw_posting: FetchedJob) -> FetchedJob:
        return raw_posting


def make_run(database, sources: list[JobSource]) -> JobFetchRun:
    return JobFetchRun(
        sources, JobRepository(database), CompanyRepository(database), SourceRunRepository(database)
    )


def test_jobs_are_saved_and_counted_per_source(database):
    sources = [StaticSource("indeed", [make_job("indeed", "1"), make_job("indeed", "2")])]
    [report] = make_run(database, sources).run()

    assert (report.fetched_jobs, report.new_jobs, report.already_known_jobs) == (2, 2, 0)
    assert JobRepository(database).count_jobs_by_source() == {"indeed": 2}


def test_failing_source_is_reported_and_the_next_still_runs(database):
    sources = [
        StaticSource("linkedin", [], error=RuntimeError("rate limited")),
        StaticSource("indeed", [make_job("indeed", "1")]),
    ]
    failed, succeeded = make_run(database, sources).run()

    assert failed.error == "rate limited"
    assert succeeded.error is None and succeeded.new_jobs == 1


def test_companies_linked_from_job_board_adverts_are_tracked_once(database):
    jobs = [
        make_job("indeed", "1", "https://jobs.ashbyhq.com/Perplexity/abc", company="Perplexity"),
        make_job("indeed", "2", "https://jobs.ashbyhq.com/perplexity/def", company="Perplexity"),
        make_job("indeed", "3", "https://careers.bbc.co.uk/job/1", company="BBC"),
        make_job("indeed", "4", None),
    ]
    [report] = make_run(database, [StaticSource("indeed", jobs)]).run()

    assert report.companies_added == 1
    [company] = CompanyRepository(database).list_active_companies()
    assert (company.name, company.ats_platform, company.ats_board_slug) == (
        "Perplexity",
        AtsPlatform.ASHBY,
        "perplexity",
    )
    assert company.discovered_via == "linked from indeed"


def test_every_source_run_is_recorded_failures_included(database):
    sources = [
        StaticSource("indeed", [make_job("indeed", "1")]),
        StaticSource("linkedin", [], error=RuntimeError("site changed its layout")),
    ]
    make_run(database, sources).run()

    runs = SourceRunRepository(database)
    [indeed_run] = runs.recent_runs("indeed", limit=5)
    [linkedin_run] = runs.recent_runs("linkedin", limit=5)
    assert (indeed_run.fetched_jobs, indeed_run.new_jobs, indeed_run.error) == (1, 1, None)
    assert linkedin_run.error == "site changed its layout"
