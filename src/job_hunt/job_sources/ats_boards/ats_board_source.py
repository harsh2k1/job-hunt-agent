from abc import abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, ClassVar
from urllib.parse import SplitResult

from job_hunt.common.http_client import HttpClient, HttpRequestError, get_http_client
from job_hunt.common.logger import get_logger
from job_hunt.common.settings_schema import AtsSourceSettings, Settings
from job_hunt.job_sources.job_source import JobSource, JobSourceError
from job_hunt.job_sources.location_matching import TargetLocationMatcher
from job_hunt.job_store.company_repository import CompanyRepository
from job_hunt.job_store.models import AtsPlatform, TrackedCompany

logger = get_logger(__name__)

# what a missing board returns on every supported ATS
BOARD_NOT_FOUND_STATUS = 404


@dataclass(frozen=True)
class BoardPosting:
    """One posting from a company's board, with the company it came from."""

    company: TrackedCompany
    posting: dict[str, Any]


class AtsBoardSource(JobSource[BoardPosting]):
    """Reads the public job board of every tracked company on one ATS.

    A subclass knows its ATS's API: how to fetch a board, where a posting
    keeps its locations, how to read a posting, and how to spot a board in a
    URL. The source's name is its platform's value.
    """

    ats_platform: ClassVar[AtsPlatform]
    # where this ATS hosts company boards, for site: searches that find new
    # companies; a new ATS is searched as soon as it has a reader
    search_domains: ClassVar[tuple[str, ...]]
    reads_tracked_companies = True
    listing_rank = 10

    def __init_subclass__(cls, **kwargs: Any):
        super().__init_subclass__(**kwargs)
        if "ats_platform" in cls.__dict__:
            cls.name = cls.ats_platform.value

    def __init__(
        self,
        source_settings: AtsSourceSettings,
        location_matcher: TargetLocationMatcher,
        http_client: HttpClient,
        company_repository: CompanyRepository,
    ):
        self._source_settings = source_settings
        self._location_matcher = location_matcher
        self._http_client = http_client
        self._company_repository = company_repository

    @classmethod
    def from_settings(cls, settings: Settings) -> "AtsBoardSource":
        return cls(
            source_settings=getattr(settings.job_sources, cls.name),
            location_matcher=TargetLocationMatcher(settings.job_search.target_location_keywords),
            http_client=get_http_client(),
            company_repository=CompanyRepository(),
        )

    # The board slug if this URL is one of this ATS's boards or postings,
    # otherwise None.
    @classmethod
    @abstractmethod
    def board_slug_from_url(cls, url: SplitResult) -> str | None: ...

    @abstractmethod
    def _fetch_board_postings(self, board_slug: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def _posting_locations(self, posting: dict[str, Any]) -> list[str | None]: ...

    def _list_raw_postings(self) -> Iterator[BoardPosting]:
        companies = self._company_repository.list_active_companies(self.ats_platform)
        failed_boards = 0
        for company in companies:
            try:
                postings = self._fetch_board_postings(company.ats_board_slug)
            except HttpRequestError as error:
                if error.status_code == BOARD_NOT_FOUND_STATUS:
                    self._deactivate_missing_board(company)
                    continue
                failed_boards += 1
                logger.warning("%s: couldn't read %s's board: %s", self.name, company.name, error)
                continue
            logger.info("%s: %d postings on %s's board", self.name, len(postings), company.name)
            for posting in postings:
                yield BoardPosting(company, posting)
        if companies and failed_boards == len(companies):
            raise JobSourceError(f"{self.name}: none of {len(companies)} boards could be read")

    def _is_in_scope(self, raw_posting: BoardPosting) -> bool:
        return self._location_matcher.matches_any(self._posting_locations(raw_posting.posting))

    # Whether a newly found board is worth tracking: one read of it, true if
    # any posting names a target location. Stricter than a fetch, which also
    # keeps postings with no location: a board is read every day once
    # tracked, so one vague posting shouldn't be enough. HttpRequestError
    # (404 for a board that's gone) is left to the caller.
    def board_has_jobs_in_target_locations(self, board_slug: str) -> bool:
        return any(
            self._location_matcher.matches(location)
            for posting in self._fetch_board_postings(board_slug)
            for location in self._posting_locations(posting)
            if location
        )

    # Posting ids are only promised unique within one board.
    @staticmethod
    def _source_job_id(company: TrackedCompany, posting_id: Any) -> str:
        return f"{company.ats_board_slug}:{posting_id}"

    # A 404 means the board is gone (company moved ATS, renamed or closed it).
    # Switched off rather than deleted, so it isn't rediscovered and re-added.
    def _deactivate_missing_board(self, company: TrackedCompany) -> None:
        logger.warning(
            "%s: %s's board %r no longer exists; deactivated", self.name, company.name, company.ats_board_slug
        )
        self._company_repository.deactivate_company(company.ats_platform, company.ats_board_slug)


# Subdomains on an ATS's own domain that are its website, not a company board
NON_BOARD_SUBDOMAINS = frozenset({"www", "app", "api", "help", "support", "blog", "docs", "developers"})


# "adarga.recruitee.com" -> "adarga" for board_domain "recruitee.com"; also
# keeps a regional part, "miracare.na.teamtailor.com" -> "miracare.na"
def company_subdomain(url: SplitResult, board_domain: str) -> str | None:
    host = url.hostname or ""
    if not host.endswith(f".{board_domain}"):
        return None
    subdomain = host[: -len(board_domain) - 1]
    return None if subdomain.split(".")[0] in NON_BOARD_SUBDOMAINS else subdomain


def first_path_segment(url: SplitResult) -> str | None:
    segments = [segment for segment in url.path.split("/") if segment]
    return segments[0] if segments else None
