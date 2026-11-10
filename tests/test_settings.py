import pytest
from pydantic import ValidationError

from job_hunt.common.settings import dynaconf_settings, settings, validate_settings
from job_hunt.common.settings_schema import HttpSettings, Settings


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


def test_settings_errors_never_show_the_values():
    raw = settings.model_dump(mode="json")
    raw["job_sources"]["misplaced"] = {"password": "do-not-print-me"}
    with pytest.raises(ValidationError) as error:
        Settings.model_validate(raw)
    assert "misplaced" in str(error.value)
    assert "do-not-print-me" not in str(error.value)
