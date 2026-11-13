from datetime import date, datetime, timezone
from enum import Enum

from pydantic import field_validator
from sqlalchemy import Enum as EnumColumnType
from sqlalchemy import Text, UniqueConstraint
from sqlmodel import Field, SQLModel

# must run before any table below is defined
import job_hunt.common.database.table_naming  # noqa: F401  # isort: skip


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
    # Lever's EU data centre: separate boards (jobs.eu.lever.co) and API host
    LEVER_EU = "lever_eu"
    ASHBY = "ashby"
    WORKABLE = "workable"
    SMARTRECRUITERS = "smartrecruiters"
    WORKDAY = "workday"
    TEAMTAILOR = "teamtailor"
    RECRUITEE = "recruitee"
    PINPOINT = "pinpoint"
    PERSONIO = "personio"
    BAMBOOHR = "bamboohr"


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
    # where the source shows the advert
    url: str = Field(min_length=1)
    # the employer's own copy of the advert, when a job board links to it;
    # this is how board results reveal which ATS a company uses
    employer_url: str | None = None
    description: str | None = Field(default=None, sa_type=Text)
    posted_at: datetime | None = None
    employment_type: str | None = None
    # as the source words it, e.g. "GBP 32000-38000 yearly"
    salary_text: str | None = None
    # None when the source doesn't say
    is_remote: bool | None = None
    # the application deadline, when the source gives one; graduate schemes
    # and internships close early, so this decides what to apply to first
    closes_at: datetime | None = None

    @field_validator("posted_at", "closes_at")
    @classmethod
    def store_times_as_utc(cls, value: datetime | None) -> datetime | None:
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
    # how it got onto the list: "added by hand", "linked from indeed", ...
    discovered_via: str = Field(min_length=1)

    # Every supported ATS treats board slugs case-insensitively, and links use
    # both "Perplexity" and "perplexity"; one spelling keeps one row per board.
    @field_validator("ats_board_slug")
    @classmethod
    def normalise_board_slug(cls, slug: str) -> str:
        return slug.strip().lower()


class TrackedCompany(DiscoveredCompany, table=True):
    __tablename__ = "companies"
    __table_args__ = (UniqueConstraint("ats_platform", "ats_board_slug"),)

    id: int | None = Field(default=None, primary_key=True)
    # switched off rather than deleted, so a company that moved ATS or closed
    # its board isn't rediscovered and re-added every day
    is_active: bool = True
    added_at: datetime = Field(default_factory=utc_now)


class LicensedSponsor(SQLModel, table=True):
    """One entry of the Home Office register of licensed sponsors (Worker and
    Temporary Worker routes): an employer allowed to sponsor visas. An
    organisation appears once per route it's licensed for. The whole table
    is replaced whenever a newer register is published."""

    __tablename__ = "licensed_sponsors"

    id: int | None = Field(default=None, primary_key=True)
    organisation_name: str
    # the name after "T/A" in the register, when it gives one
    trading_name: str | None = None
    # normalise_company_name of each, for matching job adverts' employer names
    normalised_name: str = Field(index=True)
    normalised_trading_name: str | None = Field(default=None, index=True)
    town_city: str | None = None
    county: str | None = None
    # e.g. "Worker (A rating)"
    type_and_rating: str
    # e.g. "Skilled Worker", "Global Business Mobility: Senior or Specialist Worker"
    route: str
    # the date of the register file this came from
    register_date: date


class SourceRun(SQLModel, table=True):
    """One source's part in one fetch, kept to spot a source that breaks or
    quietly starts returning far fewer jobs than usual."""

    __tablename__ = "source_runs"

    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(index=True)
    started_at: datetime
    finished_at: datetime
    fetched_jobs: int = 0
    new_jobs: int = 0
    # None when the source ran to the end
    error: str | None = Field(default=None, sa_type=Text)

    @field_validator("started_at", "finished_at")
    @classmethod
    def store_times_as_utc(cls, value: datetime) -> datetime:
        return to_utc(value)


class ListingScreening(SQLModel, table=True):
    """The LLM's verdict on one listing, kept so a listing is only ever sent
    again when its advert or the candidate's profile changes."""

    __tablename__ = "listing_screenings"

    listing_key: str = Field(primary_key=True)
    # hash of what was sent (advert and profile); a different hash means
    # the verdict is out of date
    content_hash: str
    # apply / check / skip, decided in code from the facts below
    fit: str
    reason: str
    # offered / not offered / not stated
    sponsorship: str
    # the role's field as the model read it, e.g. "data science"
    role_field: str | None = None
    # graduate / internship / part-time / full-time / other, as read from
    # the advert; with part_time and starts_when_free it decides eligibility
    kind: str | None = None
    part_time: bool | None = None
    # yes / no / not stated: does it start once the candidate is free?
    starts_when_free: str | None = None
    # bullets on why the candidate can apply and how it matches; None when
    # they can't (or it can't be told)
    valid_because: str | None = Field(default=None, sa_type=Text)
    # as the advert words it, e.g. "September 2027"
    start_date: str | None = None
    model: str
    screened_at: datetime = Field(default_factory=utc_now)

    @field_validator("screened_at")
    @classmethod
    def store_time_as_utc(cls, value: datetime) -> datetime:
        return to_utc(value)


class TitleScreening(SQLModel, table=True):
    """Whether a job title is related to the candidate's fields at all, as
    the LLM judged it from the title alone. Kept per title, so the same
    title at many companies is only ever asked about once."""

    __tablename__ = "title_screenings"

    # the title lower-cased with spaces collapsed
    title_key: str = Field(primary_key=True)
    related: bool
    # hash of the question asked; a different one means ask again
    question_hash: str
    model: str
    screened_at: datetime = Field(default_factory=utc_now)

    @field_validator("screened_at")
    @classmethod
    def store_time_as_utc(cls, value: datetime) -> datetime:
        return to_utc(value)
