from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import datetime
from typing import ClassVar, Generic, TypeVar

from pydantic import SecretStr, ValidationError

from job_hunt.common.logger import get_logger
from job_hunt.common.registry import Registry
from job_hunt.common.settings_schema import Settings
from job_hunt.job_store.models import FetchedJob

logger = get_logger(__name__)

RawPostingT = TypeVar("RawPostingT")

job_source_registry: Registry[type["JobSource"]] = Registry("job source")

# Some adverts leave the employer out; the job is kept so it can be checked by
# hand rather than silently lost.
UNKNOWN_COMPANY_NAME = "Unknown"


class JobSourceError(RuntimeError):
    """A source couldn't produce any jobs at all (as opposed to skipping a few)."""


class JobSource(ABC, Generic[RawPostingT]):
    """Template for every job source: list raw postings, drop the ones out of
    scope, turn the rest into FetchedJob.

    A subclass only says where postings come from and how to read one; the
    skipping and error handling around that are the same everywhere. New
    sources register with `job_source_registry` under the name of their
    settings block in `job_sources`.
    """

    # registered name; also stored on every job as FetchedJob.source
    name: ClassVar[str]
    # how people know the site, shown on the sheet ("LinkedIn", "jobs.ac.uk")
    display_name: ClassVar[str]
    # Sources that read the tracked-company list run after the ones that add
    # to it, so a company found on a job board is read in the same run.
    reads_tracked_companies: ClassVar[bool] = False
    # Sources that work from jobs other sources stored run after those,
    # and before the ones reading tracked companies (they may add some).
    reads_stored_jobs: ClassVar[bool] = False
    # When several sources list the same advert, the lowest rank is shown:
    # the more direct the source, the more complete and current its copy.
    # 10 company ATS boards, 20 big employers' careers sites, 30 advert-page
    # sites, 40 student portals, 50 job boards, 60 Google Jobs, 70 alert
    # emails, 80 forum threads.
    listing_rank: ClassVar[int] = 50

    @classmethod
    @abstractmethod
    def from_settings(cls, settings: Settings) -> "JobSource": ...

    # A job a source stops listing has usually closed, so it drops off the
    # sheet a few days later. A source that only sees part of a site (pages
    # the user saved) returns the time from which its jobs stay listed even
    # unseen; its jobs with a deadline stay until the deadline.
    @classmethod
    def unseen_jobs_listed_since(cls, settings: Settings, now: datetime) -> datetime | None:
        return None

    # The jobs found, with when they were seen (None: now). A source reading
    # saved snapshots says when each snapshot was taken.
    def fetch_job_batches(self) -> list[tuple[datetime | None, list[FetchedJob]]]:
        return [(None, self.fetch_jobs())]

    def fetch_jobs(self) -> list[FetchedJob]:
        return self._read_postings(self._list_raw_postings())

    def _read_postings(self, raw_postings: Iterable[RawPostingT]) -> list[FetchedJob]:
        fetched_jobs = []
        for raw_posting in raw_postings:
            if not self._is_in_scope(raw_posting):
                continue
            try:
                fetched_jobs.append(self._to_fetched_job(raw_posting))
            except (ValidationError, KeyError, TypeError, ValueError) as error:
                # one malformed posting shouldn't cost the rest of the batch
                logger.warning("%s: skipped a posting it couldn't read: %s", self.name, error)
        return fetched_jobs

    @abstractmethod
    def _list_raw_postings(self) -> Iterable[RawPostingT]: ...

    @abstractmethod
    def _to_fetched_job(self, raw_posting: RawPostingT) -> FetchedJob: ...

    # Default: the source's own query already limits scope (e.g. a location
    # search). Sources that return everything override this.
    def _is_in_scope(self, raw_posting: RawPostingT) -> bool:
        return True


JobSourceClassT = TypeVar("JobSourceClassT", bound=type[JobSource])


# Class decorator: registers a source under its own `name`, so the name is
# written once.
def register_job_source(source_class: JobSourceClassT) -> JobSourceClassT:
    job_source_registry.register(source_class.name)(source_class)
    return source_class


# Keys live in .secrets.yaml and default to null, so a source that needs one
# says exactly where it goes instead of failing on the first request.
def require_secret(source_name: str, secret: SecretStr | None, setting_name: str) -> str:
    if secret is None:
        raise JobSourceError(
            f"{source_name}: no {setting_name}; add it under job_sources.{source_name} in .secrets.yaml"
        )
    return secret.get_secret_value()
