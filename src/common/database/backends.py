from abc import ABC, abstractmethod
from sqlite3 import Connection as SqliteConnection
from typing import Any

from sqlalchemy import URL, Engine, event

from common.registry import Registry
from common.settings_schema import DatabaseSettings

database_backend_registry: Registry[type["DatabaseBackend"]] = Registry("database backend")


class DatabaseBackend(ABC):
    """Everything that differs between databases: the connection URL, engine
    options, and any setup before or after the engine exists.

    A new database is one subclass registered under the name used for
    `database.backend` in settings.yaml.
    """

    def __init__(self, database_settings: DatabaseSettings):
        self._settings = database_settings

    @abstractmethod
    def build_connection_url(self) -> URL: ...

    def build_engine_options(self) -> dict[str, Any]:
        # pre-ping swaps out connections the server has dropped while idle
        return {"echo": self._settings.echo_sql, "pool_pre_ping": True}

    # runs before the engine is created, e.g. to make the folder a file lives in
    def prepare_storage(self) -> None:
        return None

    # runs once the engine exists, e.g. to add per-connection settings
    def configure_engine(self, engine: Engine) -> None:
        return None


@database_backend_registry.register("sqlite")
class SqliteBackend(DatabaseBackend):
    def build_connection_url(self) -> URL:
        return URL.create("sqlite", database=str(self._settings.sqlite.path))

    def prepare_storage(self) -> None:
        self._settings.sqlite.path.parent.mkdir(parents=True, exist_ok=True)

    def configure_engine(self, engine: Engine) -> None:
        event.listen(engine, "connect", apply_sqlite_connection_pragmas)


# SQLite turns these off by default, and they apply per connection
def apply_sqlite_connection_pragmas(connection: SqliteConnection, _connection_record: Any) -> None:
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    # lets the dashboard read while a fetch run is writing
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


# needs the `postgres` extra: pip install -e ".[postgres]"
@database_backend_registry.register("postgres")
class PostgresBackend(DatabaseBackend):
    def build_connection_url(self) -> URL:
        postgres = self._settings.postgres
        return URL.create(
            "postgresql+psycopg",
            username=postgres.username,
            password=postgres.password.get_secret_value() if postgres.password else None,
            host=postgres.host,
            port=postgres.port,
            database=postgres.database_name,
        )

    def build_engine_options(self) -> dict[str, Any]:
        return super().build_engine_options() | {
            "pool_size": self._settings.postgres.pool_size,
            "max_overflow": self._settings.postgres.max_overflow,
        }
