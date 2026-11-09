import pytest
from pydantic import ValidationError

from common.settings import dynaconf_settings, settings, validate_settings
from common.settings_schema import HttpSettings


def test_settings_load_the_testing_environment():
    assert settings.env == "testing"
    # testing overrides apply on top of default
    assert settings.http.backoff_seconds == 0
    # and keys it doesn't override still come from default
    assert settings.http.timeout_seconds == 30


def test_paths_are_resolved_inside_the_repo():
    assert ".pytest_tmp" in str(settings.database.sqlite.path)
    assert "{" not in str(settings.cache.disk.dir)


def test_validate_settings_matches_the_singleton():
    assert validate_settings(dynaconf_settings) == settings


def test_misspelled_key_is_rejected():
    values = settings.http.model_dump() | {"timout_seconds": 5}
    with pytest.raises(ValidationError):
        HttpSettings.model_validate(values)
