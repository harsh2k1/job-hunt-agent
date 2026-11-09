from datetime import datetime, timezone
from enum import Enum

from pydantic import field_validator
from sqlalchemy import Enum as EnumColumnType
from sqlalchemy import Text, UniqueConstraint
from sqlmodel import Field, SQLModel

# must run before any table below is defined
import common.database.table_naming  # noqa: F401  # isort: skip


# Every datetime is timezone-aware UTC (SQLModel refuses naive ones), so values
# from different sources compare correctly.
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_utc(value: datetime) -> datetime:
    # a source that gives no timezone almost always means UTC
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# The applicant tracking systems whose public job boards we know how to read.
# A company's board is (platform, slug), e.g. jobs.ashbyhq.com/perplexity is
# (ASHBY, "perplexity").
class AtsPlatform(str, Enum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    WORKABLE = "workable"
    SMARTRECRUITERS = "smartrecruiters"
    WORKDAY = "workday"
    TEAMTAILOR = "teamtailor"
    RECRUITEE = "recruitee"
    PINPOINT = "pinpoint"


# Stored as its value ("greenhouse") in a plain string column rather than a
# native enum type, so supporting a new ATS never needs a migration.
ATS_PLATFORM_COLUMN_TYPE = EnumColumnType(
    AtsPlatform,
    native_enum=False,
    length=32,
    values_callable=lambda platforms: [platform.value for platform in platforms],
)


# What every job source hands back. Not a table: sources build these, the
# repository decides whether each one is new.
class FetchedJob(SQLModel):
    # which source found it, e.g. "greenhouse" or "adzuna"
    source: str = Field(min_length=1)
    # the source's own id, so the same advert seen tomorrow is recognised
    source_job_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    company_name: str = Field(min_length=1)
    location: str | None = None
    url: str = Field(min_length=1)
    description: str | None = Field(default=None, sa_type=Text)
    posted_at: datetime | None = None
    employment_type: str | None = None

    @field_validator("posted_at")
    @classmethod
    def store_posted_at_as_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else to_utc(value)


class StoredJob(FetchedJob, table=True):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("source", "source_job_id"),)

    id: int | None = Field(default=None, primary_key=True)
    first_seen_at: datetime = Field(default_factory=utc_now, index=True)
    # still listed as of this time; a job that stops being seen has probably closed
    last_seen_at: datetime = Field(default_factory=utc_now)


# A company whose own job board we read directly every day.
class DiscoveredCompany(SQLModel):
    name: str = Field(min_length=1)
    ats_platform: AtsPlatform = Field(sa_type=ATS_PLATFORM_COLUMN_TYPE)
    ats_board_slug: str = Field(min_length=1)
    # how it got onto the list: "seed list", "search: serper", "sponsor register", ...
    discovered_via: str = Field(min_length=1)


class TrackedCompany(DiscoveredCompany, table=True):
    __tablename__ = "companies"
    __table_args__ = (UniqueConstraint("ats_platform", "ats_board_slug"),)

    id: int | None = Field(default=None, primary_key=True)
    # switched off rather than deleted, so a company that moved ATS or closed
    # its board isn't rediscovered and re-added every day
    is_active: bool = True
    added_at: datetime = Field(default_factory=utc_now)
