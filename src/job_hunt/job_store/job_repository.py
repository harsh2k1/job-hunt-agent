from collections.abc import Collection, Iterable
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import func, tuple_
from sqlmodel import Session, select

from job_hunt.common.database import Database, get_database
from job_hunt.job_store.models import FetchedJob, StoredJob, utc_now

# A job's identity: which source found it, and that source's id for it.
JobKey = tuple[str, str]

IDENTITY_FIELDS = frozenset({"source", "source_job_id"})
# everything else a source reports may change between sightings; taken from the
# model so a new FetchedJob field is kept up to date without touching this file
UPDATABLE_FIELDS = tuple(name for name in FetchedJob.model_fields if name not in IDENTITY_FIELDS)

# stays well under every database's limit on bound parameters per statement
LOOKUP_BATCH_SIZE = 500


class JobSaveSummary(BaseModel):
    new_jobs: int
    already_known_jobs: int


class JobRepository:
    """All reads and writes of the jobs table go through here."""

    def __init__(self, database: Database | None = None):
        self._database = database or get_database()

    def save_fetched_jobs(
        self, fetched_jobs: Iterable[FetchedJob], seen_at: datetime | None = None
    ) -> JobSaveSummary:
        seen_at = seen_at or utc_now()
        # a source can list one advert twice in a batch; the later copy wins
        fetched_jobs_by_key: dict[JobKey, FetchedJob] = {
            (job.source, job.source_job_id): job for job in fetched_jobs
        }
        with self._database.session_scope() as session:
            stored_jobs_by_key = self._find_stored_jobs(session, fetched_jobs_by_key.keys())
            for key, fetched_job in fetched_jobs_by_key.items():
                stored_job = stored_jobs_by_key.get(key)
                if stored_job is None:
                    session.add(
                        StoredJob(**fetched_job.model_dump(), first_seen_at=seen_at, last_seen_at=seen_at)
                    )
                else:
                    self._apply_new_sighting(stored_job, fetched_job, seen_at)
        return JobSaveSummary(
            new_jobs=len(fetched_jobs_by_key) - len(stored_jobs_by_key),
            already_known_jobs=len(stored_jobs_by_key),
        )

    def list_jobs_first_seen_since(self, since: datetime) -> list[StoredJob]:
        with self._database.session_scope() as session:
            statement = (
                select(StoredJob)
                .where(StoredJob.first_seen_at >= since)
                .order_by(StoredJob.first_seen_at.desc())
            )
            return list(session.exec(statement).all())

    # still being advertised: a source saw it on a recent run
    def list_jobs_seen_since(self, since: datetime) -> list[StoredJob]:
        with self._database.session_scope() as session:
            statement = select(StoredJob).where(StoredJob.last_seen_at >= since)
            return list(session.exec(statement).all())

    def count_jobs_by_source(self) -> dict[str, int]:
        with self._database.session_scope() as session:
            rows = session.exec(select(StoredJob.source, func.count()).group_by(StoredJob.source)).all()
            return dict(rows)

    @staticmethod
    def _find_stored_jobs(session: Session, keys: Collection[JobKey]) -> dict[JobKey, StoredJob]:
        key_list = list(keys)
        stored_jobs_by_key: dict[JobKey, StoredJob] = {}
        for batch_start in range(0, len(key_list), LOOKUP_BATCH_SIZE):
            key_batch = key_list[batch_start : batch_start + LOOKUP_BATCH_SIZE]
            statement = select(StoredJob).where(
                tuple_(StoredJob.source, StoredJob.source_job_id).in_(key_batch)
            )
            for stored_job in session.exec(statement):
                stored_jobs_by_key[(stored_job.source, stored_job.source_job_id)] = stored_job
        return stored_jobs_by_key

    @staticmethod
    def _apply_new_sighting(stored_job: StoredJob, fetched_job: FetchedJob, seen_at: datetime) -> None:
        for field_name in UPDATABLE_FIELDS:
            setattr(stored_job, field_name, getattr(fetched_job, field_name))
        stored_job.last_seen_at = seen_at
