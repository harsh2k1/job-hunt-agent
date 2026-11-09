import logging
from logging.handlers import RotatingFileHandler

from job_hunt.common.settings import settings

# Every module logs under this one parent, so level and format are set in one
# place (settings.yaml) and each line still says which module it came from.
PROJECT_LOGGER_NAME = "job_hunt"


def configure_project_logger() -> logging.Logger:
    project_logger = logging.getLogger(PROJECT_LOGGER_NAME)
    if project_logger.handlers:
        return project_logger

    formatter = logging.Formatter(settings.logging.format, datefmt=settings.logging.date_format)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    project_logger.addHandler(console_handler)

    settings.logging.file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        settings.logging.file,
        maxBytes=settings.logging.file_max_megabytes * 1024 * 1024,
        backupCount=settings.logging.file_backup_count,
    )
    file_handler.setFormatter(formatter)
    project_logger.addHandler(file_handler)

    project_logger.setLevel(settings.logging.level)
    # keep our lines out of the root logger, which libraries may configure too
    project_logger.propagate = False
    return project_logger


# Modules pass __name__, which already starts with "job_hunt." inside the
# package; anything else (e.g. migrations/env.py) is put under it.
def get_logger(module_name: str) -> logging.Logger:
    configure_project_logger()
    if module_name == PROJECT_LOGGER_NAME or module_name.startswith(f"{PROJECT_LOGGER_NAME}."):
        return logging.getLogger(module_name)
    return logging.getLogger(f"{PROJECT_LOGGER_NAME}.{module_name}")
