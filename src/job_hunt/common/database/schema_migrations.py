from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlmodel import SQLModel

from job_hunt.common.database.database import Database
from job_hunt.common.database.database_factory import get_database
from job_hunt.common.settings import REPO_ROOT

ALEMBIC_CONFIG_PATH = REPO_ROOT / "alembic.ini"
LATEST_REVISION = "head"


# Migrations are the only thing that creates or changes tables, on every
# backend, so a schema change is always a reviewed file in migrations/versions.
def upgrade_schema_to_latest(database: Database | None = None) -> None:
    database = database or get_database()
    alembic_config = Config(str(ALEMBIC_CONFIG_PATH))
    with database.engine.begin() as connection:
        alembic_config.attributes["connection"] = connection
        command.upgrade(alembic_config, LATEST_REVISION)


# Differences between the models and the migrated schema. Empty means every
# model change has a migration; anything else means one still needs writing.
def find_models_missing_from_migrations(database: Database | None = None) -> list[tuple]:
    database = database or get_database()
    with database.engine.connect() as connection:
        return compare_metadata(MigrationContext.configure(connection), SQLModel.metadata)
