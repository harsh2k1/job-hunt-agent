from job_hunt.common.settings import settings as app_settings
from job_hunt.common.settings_schema import Settings

# imported so every source registers itself before the factory is used
import job_hunt.job_sources.ats_boards  # noqa: F401  # isort: skip
import job_hunt.job_sources.job_boards  # noqa: F401  # isort: skip
from job_hunt.job_sources.job_source import JobSource, job_source_registry


class JobSourceFactory:
    """Builds job sources by name, configured from settings."""

    @staticmethod
    def create(source_name: str, settings: Settings = app_settings) -> JobSource:
        return job_source_registry.get(source_name).from_settings(settings)

    @staticmethod
    def is_enabled(source_name: str, settings: Settings = app_settings) -> bool:
        return getattr(settings.job_sources, source_name).enabled

    @staticmethod
    def registered_source_names() -> list[str]:
        return job_source_registry.names()

    # Every enabled source, or just the named ones (enabled or not, so a
    # switched-off source can still be run by hand). Sources that add companies
    # come before sources that read them.
    @classmethod
    def create_sources(
        cls, source_names: list[str] | None = None, settings: Settings = app_settings
    ) -> list[JobSource]:
        if source_names is None:
            source_names = [name for name in job_source_registry.names() if cls.is_enabled(name, settings)]
        sources = [cls.create(name, settings) for name in source_names]
        return sorted(sources, key=lambda source: source.reads_tracked_companies)
