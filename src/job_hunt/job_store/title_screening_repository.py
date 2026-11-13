from collections.abc import Iterable

from sqlmodel import col, select

from job_hunt.common.database import Database, get_database
from job_hunt.job_store.models import TitleScreening


class TitleScreeningRepository:
    """All reads and writes of the title_screenings table."""

    def __init__(self, database: Database | None = None):
        self._database = database or get_database()

    # only answers to the current question count
    def find(self, title_keys: Iterable[str], question_hash: str) -> dict[str, bool]:
        keys = list(dict.fromkeys(title_keys))
        found: dict[str, bool] = {}
        with self._database.session_scope() as session:
            for start in range(0, len(keys), 500):
                rows = session.exec(
                    select(TitleScreening).where(
                        col(TitleScreening.title_key).in_(keys[start : start + 500]),
                        TitleScreening.question_hash == question_hash,
                    )
                ).all()
                found.update((row.title_key, row.related) for row in rows)
        return found

    def save(self, screenings: Iterable[TitleScreening]) -> None:
        with self._database.session_scope() as session:
            for screening in screenings:
                session.merge(screening)
