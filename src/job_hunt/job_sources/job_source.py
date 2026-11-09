from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import ClassVar, Generic, TypeVar

from pydantic import ValidationError

from job_hunt.common.logger import get_logger
from job_hunt.common.registry import Registry
from job_hunt.common.settings_schema import Settings
from job_hunt.job_store.models import FetchedJob

logger = get_logger(__name__)

RawPostingT = TypeVar("RawPostingT")

job_source_registry: Registry[type["JobSource"]] = Registry("job source")


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
    # Sources that read the tracked-company list run after the ones that add
    # to it, so a company found on a job board is read in the same run.
    reads_tracked_companies: ClassVar[bool] = False

    @classmethod
    @abstractmethod
    def from_settings(cls, settings: Settings) -> "JobSource": ...

    def fetch_jobs(self) -> list[FetchedJob]:
        fetched_jobs = []
        for raw_posting in self._list_raw_postings():
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
