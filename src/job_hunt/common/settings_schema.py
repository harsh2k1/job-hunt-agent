import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


# The shape of config/settings.yaml. No defaults here: the YAML is the one place
# defaults live. extra="forbid" so a misspelled key fails at startup instead of
# being silently ignored.
class StrictModel(BaseModel):
    # hide_input_in_errors: a settings error names the key, never its value,
    # since the values include passwords and API keys from .secrets.yaml
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


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


class SearchApiSourceSettings(StrictModel):
    enabled: bool
    # the API's own page size limit applies (Adzuna 50, Reed 100)
    results_per_page: int = Field(gt=0, le=100)
    # a search stops early once a page comes back short
    pages_per_search: int = Field(ge=1)
    # free plans count requests, so a rerun on the same day reads the cache
    cache_ttl_hours: float = Field(gt=0)


class AdzunaSourceSettings(SearchApiSourceSettings):
    # both set in config/.secrets.yaml; free at developer.adzuna.com
    app_id: SecretStr | None
    app_key: SecretStr | None
    # Adzuna's country code in the URL, e.g. gb
    country_code: str = Field(pattern=r"^[a-z]{2}$")


class ReedSourceSettings(SearchApiSourceSettings):
    # set in config/.secrets.yaml; free at reed.co.uk/developers
    api_key: SecretStr | None


class AmazonJobsSourceSettings(SearchApiSourceSettings):
    # amazon.jobs country filter, ISO 3166 alpha-3 (GBR)
    country_code: str = Field(pattern=r"^[A-Z]{3}$")


class HackerNewsHiringSourceSettings(StrictModel):
    enabled: bool
    # the monthly "Who is hiring?" threads to read, newest first; one is the
    # current month, two also catches the end of last month's
    threads_to_read: int = Field(ge=1)
    cache_ttl_hours: float = Field(gt=0)


class AdvertPageSourceSettings(StrictModel):
    enabled: bool
    # How long a downloaded advert is trusted. Where the site says when a page
    # changed (a sitemap), a change is picked up straight away regardless.
    page_cache_ttl_hours: float = Field(gt=0)
    # new pages downloaded per run; the rest wait for the next run
    max_page_downloads_per_run: int = Field(gt=0)


class JoinTheIntakeSourceSettings(AdvertPageSourceSettings):
    # top-level sections whose adverts are read, e.g. graduate-schemes
    sections: list[str] = Field(min_length=1)


class JobsAcUkSourceSettings(AdvertPageSourceSettings):
    # 25 adverts a page
    pages_per_search: int = Field(ge=1)
    search_cache_ttl_hours: float = Field(gt=0)


class GradcrackerSourceSettings(StrictModel):
    enabled: bool
    # result pages saved from a browser, in one folder per day (17-11-2026)
    pages_dir: Path
    # a job with no deadline ("Ongoing") stays listed this long after the
    # last saved page that showed it; one with a deadline, until then
    listed_without_deadline_days: int = Field(ge=1)


class EmployerAdvertsSourceSettings(StrictModel):
    enabled: bool
    # the sources whose listings have no advert text and are looked up
    for_sources: list[str] = Field(min_length=1)
    # web searches a run may spend; the rest wait for the next run
    max_searches_per_run: int = Field(ge=0)
    results_per_search: int = Field(gt=0)
    seconds_between_searches: float = Field(ge=0)
    # this many failed searches in a row stops the run: the engine is pushing back
    max_consecutive_failures: int = Field(gt=0)
    # a listing with no copy found is searched again after this long
    search_again_after_days: int = Field(gt=0)
    page_cache_ttl_hours: float = Field(gt=0)
    # share of the card's title words the advert's title must have
    min_title_overlap: float = Field(gt=0, le=1)
    # results never read: job boards read as sources anyway, or that need a login
    skipped_hosts: list[str]
    # as long as the listing it copies (see gradcracker)
    listed_without_deadline_days: int = Field(ge=1)


class EmailAlertsSourceSettings(StrictModel):
    enabled: bool
    # the OAuth client downloaded from Google Cloud (gitignored)
    gmail_client_secret_path: Path
    # written after the one-time consent; refreshed on its own after that
    gmail_token_path: Path
    # how long a run waits for the consent page to be approved
    gmail_consent_timeout_seconds: int = Field(gt=0)
    # Gmail search syntax: which emails count as job alerts
    gmail_query: str
    max_messages_per_run: int = Field(gt=0)
    message_cache_ttl_hours: float = Field(gt=0)


class KclTargetConnectSourceSettings(StrictModel):
    enabled: bool
    # the saved browser profile that keeps the King's session
    browser_profile_dir: Path
    # how long King's single sign-on gets to sign in on its own
    silent_sign_in_seconds: float = Field(gt=0)
    # When the session has ended: open a visible window for a person to sign
    # in (passkey / 2FA), and wait this long. Off for runs nobody watches.
    allow_visible_sign_in: bool
    sign_in_wait_seconds: float = Field(gt=0)
    # 100 jobs a page
    max_result_pages: int = Field(ge=1)


class AtsSourceSettings(StrictModel):
    enabled: bool


class WorkdaySourceSettings(AtsSourceSettings):
    # one page per job: cached this long, and at most this many new ones per
    # board per run (the rest come on later runs)
    page_cache_ttl_hours: float = Field(gt=0)
    max_page_downloads_per_board: int = Field(gt=0)
    # job pages read when deciding whether a newly found board has UK jobs
    board_check_sample_pages: int = Field(gt=0)


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
    adzuna: AdzunaSourceSettings
    reed: ReedSourceSettings
    hacker_news_hiring: HackerNewsHiringSourceSettings
    microsoft_careers: SearchApiSourceSettings
    amazon_jobs: AmazonJobsSourceSettings
    jointheintake: JoinTheIntakeSourceSettings
    jobs_ac_uk: JobsAcUkSourceSettings
    student_circus: AdvertPageSourceSettings
    meta_careers: AdvertPageSourceSettings
    email_alerts: EmailAlertsSourceSettings
    gradcracker: GradcrackerSourceSettings
    employer_adverts: EmployerAdvertsSourceSettings
    kcl_target_connect: KclTargetConnectSourceSettings
    greenhouse: AtsSourceSettings
    lever: AtsSourceSettings
    lever_eu: AtsSourceSettings
    ashby: AtsSourceSettings
    workable: AtsSourceSettings
    smartrecruiters: SmartRecruitersSourceSettings
    teamtailor: AtsSourceSettings
    recruitee: AtsSourceSettings
    pinpoint: AtsSourceSettings
    personio: AtsSourceSettings
    bamboohr: AtsSourceSettings
    workday: WorkdaySourceSettings


class DdgsSearchSettings(StrictModel):
    # ddgs region code, e.g. uk-en
    region: str
    # which engines ddgs asks; "auto" lets it pick and fall back
    backend: str
    timeout_seconds: int = Field(gt=0)


class CommonCrawlDiscoverySettings(StrictModel):
    # each monthly crawl samples the web; a few together find more boards
    crawls_to_read: int = Field(ge=1)
    # each new board is read once to see if it has target-location jobs
    max_board_checks_per_run: int = Field(gt=0)
    # boards without such jobs are left alone this long, then checked again
    board_recheck_after_hours: float = Field(gt=0)
    # a crawl's index never changes; its board list is kept this long
    board_list_cache_ttl_hours: float = Field(gt=0)


class CompanyDiscoverySettings(StrictModel):
    # registered search provider name; its block below is used
    search_provider: Literal["ddgs"]
    # short and plain: each one is searched on every ATS domain in every
    # job_search location
    role_phrases: list[str] = Field(min_length=1)
    results_per_query: int = Field(gt=0, le=50)
    # companies rarely move ATS, so a week-old search is still good
    cache_ttl_hours: float = Field(gt=0)
    # Free engines block bursts, so a run sends at most this many searches
    # and the next run carries on with the rest of the queries.
    max_searches_per_run: int = Field(gt=0)
    # this many failures in a row stops the run: the engine is pushing back
    max_consecutive_failures: int = Field(gt=0)
    seconds_between_searches: float = Field(ge=0)
    ddgs: DdgsSearchSettings
    common_crawl: CommonCrawlDiscoverySettings


class CompanySeedSettings(StrictModel):
    # reviewed list of company boards loaded by `job-hunt seed-companies`
    seed_list_path: Path


class SourceHealthSettings(StrictModel):
    # a source's usual count is the median of this many earlier good runs
    baseline_runs: int = Field(ge=1)
    # judged only once it has this many
    min_runs_for_baseline: int = Field(ge=1)
    # below this share of the usual count is a warning
    drop_warning_ratio: float = Field(gt=0, lt=1)


class PortalLogin(StrictModel):
    username: str = Field(min_length=1)
    password: SecretStr


class PortalLoginsSettings(StrictModel):
    # King's single sign-on (Microsoft), used by Target Connect; set in
    # config/.secrets.yaml, null until then
    kcl: PortalLogin | None


class TitleRulesSettings(StrictModel):
    # whole words, any case; a keep term wins over a drop term
    keep_terms: list[str] = Field(min_length=1)
    drop_terms: list[str] = Field(min_length=1)


class LlmSettings(StrictModel):
    # registered LLM client; claude_cli runs the Claude Code CLI on the
    # user's subscription (no API key)
    backend: Literal["claude_cli"]
    claude_cli_binary: str
    timeout_seconds: int = Field(gt=0)


class LlmScreeningSettings(StrictModel):
    enabled: bool
    # cheap model for every listing the rules keep; the stronger one only for
    # the ones it can't decide ("check") that have an advert text to read
    first_pass_model: str
    second_pass_model: str
    batch_size: int = Field(gt=0, le=50)
    # titles per call in the first pass (titles only, so many fit)
    title_batch_size: int = Field(gt=0, le=300)
    # what the LLM reads of an advert: its opening, plus every line from
    # anywhere in it about visas, start dates, experience and hours, so the
    # deciding lines are never cut off
    advert_opening_chars: int = Field(gt=0)
    advert_key_fact_chars: int = Field(gt=0)
    # a cap per run, to stay well inside the plan's usage limits
    max_listings_per_run: int = Field(gt=0)
    # when the candidate is free to start full-time, e.g. "September 2027";
    # before then only graduate, part-time and suitable intern roles apply
    available_full_time_from: str = Field(min_length=1)
    # when the candidate's Graduate (PSW) visa ends: graduate and full-time
    # roles run past it, so they need an employer who can sponsor
    graduate_visa_ends: str = Field(min_length=1)
    # a role is skipped for its field only if it involves none of these
    relevant_fields: list[str] = Field(min_length=1)


class GoogleSheetSettings(StrictModel):
    enabled: bool
    # the sheet shared with the service account as Editor (settings.local.yaml)
    spreadsheet_url: str | None
    # tab names: kept listings, and the ones screened out with the reason
    jobs_worksheet: str = Field(min_length=1)
    # listings the advert doesn't settle, and those not screened yet
    checks_worksheet: str = Field(min_length=1)
    skipped_worksheet: str = Field(min_length=1)
    # listings seen by a source within this many days
    listing_days: int = Field(gt=0)
    # the service account's JSON key, from .secrets.yaml; held as a secret so
    # it never shows in show-settings or an error
    service_account: SecretStr | None

    @field_validator("service_account", mode="before")
    @classmethod
    def key_as_json(cls, value: Any) -> Any:
        return json.dumps(dict(value)) if isinstance(value, Mapping) else value


class StepCadenceSettings(StrictModel):
    sponsor_register: int = Field(ge=1)
    web_search_discovery: int = Field(ge=1)
    common_crawl_discovery: int = Field(ge=1)
    fetch: int = Field(ge=1)
    source_health: int = Field(ge=1)
    llm_screening: int = Field(ge=1)
    google_sheet: int = Field(ge=1)


class DailyRunSettings(StrictModel):
    # how often each step runs, in days: 1 = every run
    every_days: StepCadenceSettings
    # when each step last ran, so a step that isn't due is skipped
    state_file: Path
    # sources fetched at the same time (requests to any one site stay spaced)
    max_parallel_sources: int = Field(ge=1)


class LaunchdSchedulingSettings(StrictModel):
    label: str = Field(min_length=1)
    app_path: Path

    @field_validator("app_path")
    @classmethod
    def expand_home(cls, value: Path) -> Path:
        return value.expanduser()


class SystemdSchedulingSettings(StrictModel):
    unit_name: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")


class TaskSchedulerSchedulingSettings(StrictModel):
    folder: str = Field(pattern=r"^[A-Za-z0-9_ -]+$")


class SchedulingSettings(StrictModel):
    # auto picks the one for this operating system
    backend: Literal["auto", "launchd", "systemd", "task_scheduler"]
    state_dir: Path
    log_dir: Path
    check_interval_minutes: int = Field(ge=1, le=60)
    on_time_grace_minutes: int = Field(ge=1)
    internet_wait_seconds: int = Field(ge=0)
    run_time_limit_minutes: int = Field(ge=1)
    attempts_per_slot: int = Field(ge=1)
    launchd: LaunchdSchedulingSettings
    systemd: SystemdSchedulingSettings
    task_scheduler: TaskSchedulerSchedulingSettings


class Settings(StrictModel):
    env: str
    logging: LoggingSettings
    database: DatabaseSettings
    cache: CacheSettings
    http: HttpSettings
    job_search: JobSearchSettings
    job_sources: JobSourcesSettings
    companies: CompanySeedSettings
    company_discovery: CompanyDiscoverySettings
    portal_logins: PortalLoginsSettings
    source_health: SourceHealthSettings
    title_rules: TitleRulesSettings
    llm: LlmSettings
    llm_screening: LlmScreeningSettings
    google_sheet: GoogleSheetSettings
    scheduling: SchedulingSettings
    daily_run: DailyRunSettings
