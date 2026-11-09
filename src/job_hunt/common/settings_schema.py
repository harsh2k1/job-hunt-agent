from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


# The shape of config/settings.yaml. No defaults here: the YAML is the one place
# defaults live. extra="forbid" so a misspelled key fails at startup instead of
# being silently ignored.
class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LoggingSettings(StrictModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]
    format: str
    date_format: str
    file: Path
    file_max_megabytes: int = Field(gt=0)
    file_backup_count: int = Field(ge=0)


class SqliteSettings(StrictModel):
    path: Path


class PostgresSettings(StrictModel):
    host: str
    port: int = Field(gt=0, le=65535)
    database_name: str
    username: str
    # None for setups that authenticate without one (peer, trust, .pgpass)
    password: SecretStr | None
    pool_size: int = Field(gt=0)
    max_overflow: int = Field(ge=0)


# Each backend keeps its own block so switching is a one-word change to
# `backend`; the unused block is ignored.
class DatabaseSettings(StrictModel):
    backend: Literal["sqlite", "postgres"]
    echo_sql: bool
    sqlite: SqliteSettings
    postgres: PostgresSettings


class DiskCacheSettings(StrictModel):
    dir: Path
    size_limit_megabytes: int = Field(gt=0)


class MemoryCacheSettings(StrictModel):
    max_entries: int = Field(gt=0)


class CacheSettings(StrictModel):
    backend: Literal["disk", "memory"]
    # put in front of every key; changing it drops every existing entry at once
    key_prefix: str = Field(min_length=1)
    default_ttl_hours: float = Field(gt=0)
    disk: DiskCacheSettings
    memory: MemoryCacheSettings


class HttpSettings(StrictModel):
    timeout_seconds: float = Field(gt=0)
    max_retries: int = Field(ge=0)
    backoff_seconds: float = Field(ge=0)
    min_seconds_between_requests_to_same_host: float = Field(ge=0)
    user_agent: str = Field(min_length=1)


class JobSearchSettings(StrictModel):
    # what job boards are searched for: every term in every location
    role_terms: list[str] = Field(min_length=1)
    locations: list[str] = Field(min_length=1)
    # Company job boards list every office worldwide; a posting is kept if any
    # of its locations mentions one of these (whole words, any case).
    target_location_keywords: list[str] = Field(min_length=1)
    # job boards only; a company's own board is read in full every time
    max_posting_age_hours: int = Field(gt=0)
    # Indeed and Glassdoor regional site, as JobSpy names it
    indeed_country: str
    search_radius_miles: int = Field(gt=0)


class JobBoardSourceSettings(StrictModel):
    enabled: bool
    results_per_search: int = Field(gt=0)


class LinkedInSourceSettings(JobBoardSourceSettings):
    # one extra request per job, so slower and closer to LinkedIn's rate limit
    fetch_descriptions: bool


class GoogleJobsSourceSettings(JobBoardSourceSettings):
    # Google Jobs takes one free-text query; {role} and {location} are filled in
    search_term_template: str = Field(pattern=r"\{role\}.*\{location\}|\{location\}.*\{role\}")


class SerpApiGoogleJobsSourceSettings(StrictModel):
    enabled: bool
    # set in config/.secrets.yaml; the source refuses to run without it
    api_key: SecretStr | None
    # Its own, shorter list: every search costs one of the plan's monthly
    # searches (250 on the free plan), so the job_search role terms would
    # use the month up in about two weeks.
    search_terms: list[str] = Field(min_length=1)
    # where Google should search from, in SerpApi's location format
    location: str
    google_domain: str
    country_code: str = Field(pattern=r"^[a-z]{2}$")
    # each extra page of 10 results is one more search
    pages_per_search: int = Field(ge=1)
    # the run is skipped if it would leave fewer searches than this
    searches_kept_in_reserve: int = Field(ge=0)
    # a rerun on the same day reads the cache instead of spending searches
    cache_ttl_hours: float = Field(gt=0)


class AtsSourceSettings(StrictModel):
    enabled: bool


class SmartRecruitersSourceSettings(AtsSourceSettings):
    # SmartRecruiters filters by country server-side (ISO code, lower case);
    # big employers have thousands of postings worldwide
    country_code: str = Field(pattern=r"^[a-z]{2}$")
    page_size: int = Field(gt=0, le=100)


# One block per source; the block name is the source's registered name.
class JobSourcesSettings(StrictModel):
    indeed: JobBoardSourceSettings
    linkedin: LinkedInSourceSettings
    glassdoor: JobBoardSourceSettings
    google_jobs: GoogleJobsSourceSettings
    serpapi_google_jobs: SerpApiGoogleJobsSourceSettings
    greenhouse: AtsSourceSettings
    lever: AtsSourceSettings
    lever_eu: AtsSourceSettings
    ashby: AtsSourceSettings
    workable: AtsSourceSettings
    smartrecruiters: SmartRecruitersSourceSettings


class CompanySeedSettings(StrictModel):
    # reviewed list of company boards loaded by `job-hunt seed-companies`
    seed_list_path: Path


class Settings(StrictModel):
    env: str
    logging: LoggingSettings
    database: DatabaseSettings
    cache: CacheSettings
    http: HttpSettings
    job_search: JobSearchSettings
    job_sources: JobSourcesSettings
    companies: CompanySeedSettings
