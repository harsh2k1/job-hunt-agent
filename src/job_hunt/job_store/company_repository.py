from sqlmodel import Session, select

from job_hunt.common.database import Database, get_database
from job_hunt.job_store.models import AtsPlatform, DiscoveredCompany, TrackedCompany


class CompanyNotFoundError(LookupError):
    pass


class CompanyRepository:
    """All reads and writes of the companies table go through here."""

    def __init__(self, database: Database | None = None):
        self._database = database or get_database()

    # True if the company was added, False if its board was already tracked
    # (including boards that were deactivated, so they aren't brought back)
    def add_company_if_new(self, company: DiscoveredCompany) -> bool:
        with self._database.session_scope() as session:
            if self._find_company(session, company.ats_platform, company.ats_board_slug) is not None:
                return False
            session.add(TrackedCompany(**company.model_dump()))
            return True

    # deactivated boards count as known too, for the same reason
    def is_known(self, ats_platform: AtsPlatform, ats_board_slug: str) -> bool:
        with self._database.session_scope() as session:
            return self._find_company(session, ats_platform, ats_board_slug) is not None

    def list_active_companies(self, ats_platform: AtsPlatform | None = None) -> list[TrackedCompany]:
        with self._database.session_scope() as session:
            statement = select(TrackedCompany).where(TrackedCompany.is_active)
            if ats_platform is not None:
                statement = statement.where(TrackedCompany.ats_platform == ats_platform)
            return list(session.exec(statement.order_by(TrackedCompany.name)).all())

    def deactivate_company(self, ats_platform: AtsPlatform, ats_board_slug: str) -> None:
        with self._database.session_scope() as session:
            company = self._find_company(session, ats_platform, ats_board_slug)
            if company is None:
                raise CompanyNotFoundError(
                    f"no tracked company on {ats_platform.value} board {ats_board_slug!r}"
                )
            company.is_active = False

    @staticmethod
    def _find_company(
        session: Session, ats_platform: AtsPlatform, ats_board_slug: str
    ) -> TrackedCompany | None:
        statement = select(TrackedCompany).where(
            TrackedCompany.ats_platform == ats_platform,
            TrackedCompany.ats_board_slug == ats_board_slug,
        )
        return session.exec(statement).first()
