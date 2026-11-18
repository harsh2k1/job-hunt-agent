from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from itertools import groupby

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


@dataclass
class _Fetched:
    started_at: datetime
    finished_at: datetime
    batches: list[tuple[datetime | None, list[FetchedJob]]]
    error: str | None = None


# which sources must wait for others: board readers and feeds first, then
# those working from stored jobs, then those reading the tracked companies
# (which the earlier ones may add to)
def _phase(source: JobSource) -> tuple[bool, bool]:
    return (source.reads_tracked_companies, source.reads_stored_jobs)


class JobFetchRun:
    """Fetches from every source, saves what each finds, and adds any company
    board a job advert links to.

    Sources go in phases (see _phase); within a phase up to max_parallel of
    them fetch at the same time, since they read different sites. Saving
    happens one source at a time, in the order given, once its phase is
    done. One source failing never stops the others.
    """

    def __init__(
        self,
        sources: list[JobSource],
        job_repository: JobRepository | None = None,
        company_repository: CompanyRepository | None = None,
        run_repository: SourceRunRepository | None = None,
        max_parallel: int = 1,
    ):
        self._sources = sources
        self._max_parallel = max_parallel
        self._job_repository = job_repository or JobRepository()
        self._company_repository = company_repository or CompanyRepository()
        self._run_repository = run_repository or SourceRunRepository()

    # every source's result is recorded, failures included, so its health
    # can be judged against its own history
    def run(self) -> list[SourceFetchReport]:
        reports = []
        ordered = sorted(self._sources, key=_phase)
        with ThreadPoolExecutor(max_workers=self._max_parallel) as pool:
            for _, phase in groupby(ordered, key=_phase):
                sources = list(phase)
                for source, fetched in zip(sources, pool.map(self._fetch, sources), strict=True):
                    report = self._save(source, fetched)
                    self._run_repository.record(
                        SourceRun(
                            source=source.name,
                            started_at=fetched.started_at,
                            finished_at=fetched.finished_at,
                            fetched_jobs=report.fetched_jobs,
                            new_jobs=report.new_jobs,
                            error=report.error,
                        )
                    )
                    reports.append(report)
        return reports

    # runs in a worker thread: only reads, so nothing is written from two at once
    def _fetch(self, source: JobSource) -> _Fetched:
        logger.info("%s: fetching", source.name)
        started_at = utc_now()
        try:
            return _Fetched(started_at, utc_now(), source.fetch_job_batches())
        # the run's outer boundary: whatever broke inside a source is logged
        # with its traceback and reported, and the other sources still run
        except Exception as error:
            logger.exception("%s: failed", source.name)
            return _Fetched(started_at, utc_now(), [], str(error) or type(error).__name__)

    def _save(self, source: JobSource, fetched: _Fetched) -> SourceFetchReport:
        if fetched.error is not None:
            return SourceFetchReport(source_name=source.name, error=fetched.error)
        batches = fetched.batches
        # oldest batch first, so each job ends up last seen in the newest one
        new_jobs = 0
        for seen_at, batch in batches:
            new_jobs += self._job_repository.save_fetched_jobs(batch, seen_at).new_jobs
        fetched_jobs = list(
            {(job.source, job.source_job_id): job for _, batch in batches for job in batch}.values()
        )
        # an ATS source's links point at boards that are already tracked
        companies_added = (
            0 if source.reads_tracked_companies else self._add_linked_companies(source.name, fetched_jobs)
        )
        report = SourceFetchReport(
            source_name=source.name,
            fetched_jobs=len(fetched_jobs),
            new_jobs=new_jobs,
            already_known_jobs=len(fetched_jobs) - new_jobs,
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
