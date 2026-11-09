import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlmodel import select

from job_hunt.common.database import DatabaseFactory
from job_hunt.common.database.backends import PostgresBackend, database_backend_registry
from job_hunt.common.database.schema_migrations import (
    find_models_missing_from_migrations,
    upgrade_schema_to_latest,
)
from job_hunt.common.registry import UnknownRegistryEntryError
from job_hunt.common.settings import settings
from job_hunt.common.settings_schema import PostgresSettings, SqliteSettings
from job_hunt.job_store.models import AtsPlatform, TrackedCompany


def test_sqlite_database_is_created_with_its_folder(tmp_path):
    database_path = tmp_path / "nested" / "folder" / "jobs.db"
    database_settings = settings.database.model_copy(update={"sqlite": SqliteSettings(path=database_path)})
    database = DatabaseFactory.create(database_settings)
    upgrade_schema_to_latest(database)

    assert database_path.exists()
    database.dispose()


def test_every_model_change_has_a_migration(database):
    # fails when a model changes without `alembic revision --autogenerate`
    assert find_models_missing_from_migrations(database) == []


def test_migrations_can_be_rerun(database):
    upgrade_schema_to_latest(database)


def test_sqlite_connections_enforce_foreign_keys(database):
    with database.session_scope() as session:
        assert session.exec(text("PRAGMA foreign_keys")).one()[0] == 1


def test_session_scope_rolls_back_when_the_block_raises(database):
    with pytest.raises(RuntimeError), database.session_scope() as session:
        session.add(
            TrackedCompany(
                name="Wayve",
                ats_platform=AtsPlatform.GREENHOUSE,
                ats_board_slug="wayve",
                discovered_via="test",
            )
        )
        session.flush()
        raise RuntimeError("something failed halfway")

    with database.session_scope() as session:
        assert session.exec(select(TrackedCompany)).all() == []


def test_postgres_url_is_built_from_its_settings():
    postgres_settings = PostgresSettings(
        host="db.internal",
        port=6543,
        database_name="jobs",
        username="agent",
        password=SecretStr("s3cret"),
        pool_size=2,
        max_overflow=0,
    )
    backend = PostgresBackend(settings.database.model_copy(update={"postgres": postgres_settings}))
    url = backend.build_connection_url()

    assert (
        url.render_as_string(hide_password=False) == "postgresql+psycopg://agent:s3cret@db.internal:6543/jobs"
    )
    # the password must not leak into logs or error messages
    assert "s3cret" not in str(url)
    assert backend.build_engine_options()["pool_size"] == 2


def test_both_database_backends_are_registered():
    assert database_backend_registry.names() == ["postgres", "sqlite"]


def test_unknown_backend_names_the_registered_ones():
    with pytest.raises(UnknownRegistryEntryError, match="postgres, sqlite"):
        database_backend_registry.get("mysql")
