from pathlib import Path
from typing import Any

from dynaconf import Dynaconf

from job_hunt.common.settings_schema import Settings

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "config"

# dynaconf does the loading: merges the YAML files, picks the environment
# (JOBHUNT_ENV=<name>) and applies JOBHUNT_* env var overrides. Missing optional
# files (settings.local.yaml, .secrets.yaml) are skipped.
dynaconf_settings = Dynaconf(
    settings_files=[
        CONFIG_DIR / "settings.yaml",
        CONFIG_DIR / "settings.local.yaml",
        CONFIG_DIR / ".secrets.yaml",
    ],
    envvar_prefix="JOBHUNT",
    environments=True,
    env="default",
    env_switcher="JOBHUNT_ENV",
    merge_enabled=True,
    REPO_ROOT=str(REPO_ROOT),
)


# raw is a Dynaconf, typed Any because its methods are proxied through
# __getattr__ and the type checker can't see them
def validate_settings(raw: Any) -> Settings:
    # dynaconf upper-cases top-level keys; REPO_ROOT is only there for @format
    values = {key.lower(): value for key, value in raw.as_dict().items() if key != "REPO_ROOT"}
    values["env"] = raw.current_env
    return Settings.model_validate(values)


# what the rest of the code imports: typed, validated, read-only
settings = validate_settings(dynaconf_settings)
