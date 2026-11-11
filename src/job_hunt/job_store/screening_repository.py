from collections.abc import Iterable

from sqlmodel import col, select

from job_hunt.common.database import Database, get_database
from job_hunt.job_store.models import ListingScreening


class ScreeningRepository:
    """All reads and writes of the listing_screenings table."""

    def __init__(self, database: Database | None = None):
        self._database = database or get_database()

    def find(self, listing_keys: Iterable[str]) -> dict[str, ListingScreening]:
        keys = list(listing_keys)
        found: dict[str, ListingScreening] = {}
        with self._database.session_scope() as session:
            # in chunks: databases cap the parameters in one statement
            for start in range(0, len(keys), 500):
                rows = session.exec(
                    select(ListingScreening).where(
                        col(ListingScreening.listing_key).in_(keys[start : start + 500])
                    )
                ).all()
                found.update((row.listing_key, row) for row in rows)
        return found

    # a newer verdict replaces the old one for the same listing
    def save(self, screenings: Iterable[ListingScreening]) -> None:
        with self._database.session_scope() as session:
            for screening in screenings:
                session.merge(screening)
