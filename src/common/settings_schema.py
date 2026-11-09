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


class Settings(StrictModel):
    env: str
    logging: LoggingSettings
    database: DatabaseSettings
    cache: CacheSettings
    http: HttpSettings
