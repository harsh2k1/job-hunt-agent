import os

# must be set before anything imports job_hunt.common.settings, which reads it once
os.environ["JOBHUNT_ENV"] = "testing"

from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402

from job_hunt.common.cache import Cache, CacheFactory  # noqa: E402
from job_hunt.common.database import Database, DatabaseFactory  # noqa: E402
from job_hunt.common.database.schema_migrations import upgrade_schema_to_latest  # noqa: E402
from job_hunt.common.settings import settings  # noqa: E402
from job_hunt.common.settings_schema import SqliteSettings  # noqa: E402


# A fresh database per test, built by running the real migrations, so the
# tests also prove the migrations work.
@pytest.fixture
def database(tmp_path) -> Iterator[Database]:
    database_settings = settings.database.model_copy(
        update={"backend": "sqlite", "sqlite": SqliteSettings(path=tmp_path / "test.db")}
    )
    test_database = DatabaseFactory.create(database_settings)
    upgrade_schema_to_latest(test_database)
    yield test_database
    test_database.dispose()


@pytest.fixture
def cache() -> Iterator[Cache]:
    test_cache = CacheFactory.create(settings.cache.model_copy(update={"backend": "memory"}))
    yield test_cache
    test_cache.close()
