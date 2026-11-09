from functools import cache

from sqlalchemy import create_engine

from job_hunt.common.database.backends import database_backend_registry
from job_hunt.common.database.database import Database
from job_hunt.common.settings import settings
from job_hunt.common.settings_schema import DatabaseSettings


class DatabaseFactory:
    """Builds a Database for whichever backend the settings name."""

    @staticmethod
    def create(database_settings: DatabaseSettings) -> Database:
        backend_class = database_backend_registry.get(database_settings.backend)
        backend = backend_class(database_settings)
        backend.prepare_storage()
        engine = create_engine(backend.build_connection_url(), **backend.build_engine_options())
        backend.configure_engine(engine)
        return Database(engine)


# the one Database the app shares; tests build their own through the factory
@cache
def get_database() -> Database:
    return DatabaseFactory.create(settings.database)
