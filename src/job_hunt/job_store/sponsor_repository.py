from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, func, or_
from sqlmodel import col, select

from job_hunt.common.database import Database, get_database
from job_hunt.job_store.company_names import normalise_company_name
from job_hunt.job_store.models import LicensedSponsor


@dataclass(frozen=True)
class SponsorMatch:
    sponsor: LicensedSponsor
    # exact: the employer's name is the register name (or trading name).
    # Otherwise the register name starts with it ("Monzo" -> "Monzo Bank
    # Ltd"), which is usually the same firm but worth a look.
    is_exact: bool


class SponsorRepository:
    """All reads and writes of the licensed_sponsors table."""

    def __init__(self, database: Database | None = None):
        self._database = database or get_database()

    # All or nothing: a half-loaded register would make sponsors vanish.
    def replace_register(self, sponsors: Iterable[LicensedSponsor]) -> int:
        with self._database.session_scope() as session:
            session.exec(delete(LicensedSponsor))
            rows = list(sponsors)
            session.add_all(rows)
            return len(rows)

    def register_date(self) -> date | None:
        with self._database.session_scope() as session:
            return session.exec(select(func.max(LicensedSponsor.register_date))).one()

    def find_sponsors(self, employer_name: str) -> list[SponsorMatch]:
        name = normalise_company_name(employer_name)
        if not name:
            return []
        # "name " up to "name " + the highest character: every longer name
        # that starts with this one as whole words, through the index
        prefix_low, prefix_high = f"{name} ", f"{name} ￿"
        with self._database.session_scope() as session:
            sponsors = session.exec(
                select(LicensedSponsor).where(
                    or_(
                        col(LicensedSponsor.normalised_name) == name,
                        col(LicensedSponsor.normalised_trading_name) == name,
                        col(LicensedSponsor.normalised_name).between(prefix_low, prefix_high),
                        col(LicensedSponsor.normalised_trading_name).between(prefix_low, prefix_high),
                    )
                )
            ).all()
        matches = [
            SponsorMatch(sponsor, name in (sponsor.normalised_name, sponsor.normalised_trading_name))
            for sponsor in sponsors
        ]
        return sorted(matches, key=lambda match: (not match.is_exact, match.sponsor.organisation_name))
