from sqlmodel import select

from job_hunt.common.database import Database, get_database
from job_hunt.job_store.models import SourceRun


class SourceRunRepository:
    """All reads and writes of the source_runs table."""

    def __init__(self, database: Database | None = None):
        self._database = database or get_database()

    def record(self, run: SourceRun) -> None:
        with self._database.session_scope() as session:
            session.add(run)

    # newest first
    def recent_runs(self, source: str, limit: int) -> list[SourceRun]:
        with self._database.session_scope() as session:
            statement = (
                select(SourceRun)
                .where(SourceRun.source == source)
                .order_by(SourceRun.started_at.desc())
                .limit(limit)
            )
            return list(session.exec(statement).all())

    def sources_with_runs(self) -> list[str]:
        with self._database.session_scope() as session:
            return sorted(session.exec(select(SourceRun.source).distinct()).all())
