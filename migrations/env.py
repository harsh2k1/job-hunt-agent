from alembic import context
from sqlalchemy import Connection
from sqlmodel import SQLModel

from job_hunt.common.database import get_database

# registers every table on SQLModel.metadata, which autogenerate compares against
import job_hunt.job_store.models  # noqa: F401  # isort: skip

target_metadata = SQLModel.metadata


def configure_migration_context(**options: object) -> None:
    context.configure(
        target_metadata=target_metadata,
        # catch column type changes, not only added and dropped columns
        compare_type=True,
        **options,
    )


def run_migrations_on(connection: Connection) -> None:
    # SQLite can't ALTER most things in place; batch mode copies the table instead
    configure_migration_context(connection=connection, render_as_batch=connection.dialect.name == "sqlite")
    with context.begin_transaction():
        context.run_migrations()


# `alembic upgrade head --sql`: print the SQL instead of running it
def run_migrations_offline() -> None:
    database_url = get_database().engine.url
    configure_migration_context(
        url=database_url.render_as_string(hide_password=False),
        literal_binds=True,
        render_as_batch=database_url.get_backend_name() == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # the app and the tests hand over their own connection; the alembic CLI doesn't
    handed_over_connection = context.config.attributes.get("connection")
    if handed_over_connection is not None:
        run_migrations_on(handed_over_connection)
        return
    with get_database().engine.connect() as connection:
        run_migrations_on(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
