from collections.abc import Iterable

from pydantic import BaseModel

from job_hunt.common.logger import get_logger
from job_hunt.job_sources.ats_boards.ats_board_links import AtsBoardLink, find_ats_board
from job_hunt.job_sources.job_source import JobSource
from job_hunt.job_store.company_repository import CompanyRepository
from job_hunt.job_store.job_repository import JobRepository
from job_hunt.job_store.models import DiscoveredCompany, FetchedJob, SourceRun, utc_now
from job_hunt.job_store.source_run_repository import SourceRunRepository

logger = get_logger(__name__)


class SourceFetchReport(BaseModel):
    source_name: str
    fetched_jobs: int = 0
    new_jobs: int = 0
    already_known_jobs: int = 0
    companies_added: int = 0
    # set when the source failed outright; its jobs from this run are lost
    error: str | None = None


class JobFetchRun:
    """Runs sources one after another, saves what each finds, and adds any
    company board a job advert links to.

    Sources run in the order given (JobSourceFactory puts the ones that add
    companies first). One source failing never stops the others.
    """

    def __init__(
        self,
        sources: list[JobSource],
        job_repository: JobRepository | None = None,
        company_repository: CompanyRepository | None = None,
        run_repository: SourceRunRepository | None = None,
    ):
        self._sources = sources
        self._job_repository = job_repository or JobRepository()
        self._company_repository = company_repository or CompanyRepository()
        self._run_repository = run_repository or SourceRunRepository()

    # every source's result is recorded, failures included, so its health
    # can be judged against its own history
    def run(self) -> list[SourceFetchReport]:
        reports = []
        for source in self._sources:
            started_at = utc_now()
            report = self._fetch_from(source)
            self._run_repository.record(
                SourceRun(
                    source=source.name,
                    started_at=started_at,
                    finished_at=utc_now(),
                    fetched_jobs=report.fetched_jobs,
                    new_jobs=report.new_jobs,
                    error=report.error,
                )
            )
            reports.append(report)
        return reports

    def _fetch_from(self, source: JobSource) -> SourceFetchReport:
        logger.info("%s: fetching", source.name)
        try:
            fetched_jobs = source.fetch_jobs()
        # the run's outer boundary: whatever broke inside a source is logged
        # with its traceback and reported, and the next source still runs
        except Exception as error:
            logger.exception("%s: failed", source.name)
            return SourceFetchReport(source_name=source.name, error=str(error) or type(error).__name__)

        save_summary = self._job_repository.save_fetched_jobs(fetched_jobs)
        # an ATS source's links point at boards that are already tracked
        companies_added = (
            0 if source.reads_tracked_companies else self._add_linked_companies(source.name, fetched_jobs)
        )
        report = SourceFetchReport(
            source_name=source.name,
            fetched_jobs=len(fetched_jobs),
            new_jobs=save_summary.new_jobs,
            already_known_jobs=save_summary.already_known_jobs,
            companies_added=companies_added,
        )
        logger.info(
            "%s: %d jobs (%d new), %d companies added",
            source.name,
            report.fetched_jobs,
            report.new_jobs,
            report.companies_added,
        )
        return report

    # A board advert that links to e.g. jobs.lever.co/acme means Acme's whole
    # board can be read directly from now on, including roles it never posted
    # to the board.
    def _add_linked_companies(self, source_name: str, fetched_jobs: Iterable[FetchedJob]) -> int:
        linked_boards: dict[AtsBoardLink, str] = {}
        for job in fetched_jobs:
            board_link = find_ats_board(job.employer_url) if job.employer_url else None
            if board_link is not None:
                linked_boards.setdefault(board_link, job.company_name)

        companies_added = 0
        for board_link, company_name in linked_boards.items():
            company = DiscoveredCompany(
                name=company_name,
                ats_platform=board_link.ats_platform,
                ats_board_slug=board_link.ats_board_slug,
                discovered_via=f"linked from {source_name}",
            )
            if self._company_repository.add_company_if_new(company):
                companies_added += 1
                logger.info(
                    "%s: now tracking %s on %s", source_name, company_name, board_link.ats_platform.value
                )
        return companies_added
