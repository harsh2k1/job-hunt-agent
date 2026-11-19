from job_hunt.common.registry import UnknownRegistryEntryError
from job_hunt.common.settings import settings as app_settings
from job_hunt.common.settings_schema import Settings

# imported so every source registers itself before the factory is used
import job_hunt.job_sources.advert_pages  # noqa: F401  # isort: skip
import job_hunt.job_sources.ats_boards  # noqa: F401  # isort: skip
import job_hunt.job_sources.email_alerts  # noqa: F401  # isort: skip
import job_hunt.job_sources.employer_adverts  # noqa: F401  # isort: skip
import job_hunt.job_sources.job_boards  # noqa: F401  # isort: skip
import job_hunt.job_sources.portals  # noqa: F401  # isort: skip
import job_hunt.job_sources.saved_pages  # noqa: F401  # isort: skip
from job_hunt.job_sources.ats_boards.ats_board_source import AtsBoardSource
from job_hunt.job_sources.ats_boards.board_schedule import BoardSchedule
from job_hunt.job_sources.job_source import JobSource, job_source_registry

UNRANKED_LISTING = 99


class JobSourceFactory:
    """Builds job sources by name, configured from settings."""

    # company-board sources read only the boards due this run (quiet ones weekly)
    @staticmethod
    def create(source_name: str, settings: Settings = app_settings) -> JobSource:
        source = job_source_registry.get(source_name).from_settings(settings)
        if isinstance(source, AtsBoardSource):
            source.board_schedule = lambda: BoardSchedule.for_source(settings, source_name)
        return source

    # rank used to pick which source's copy of an advert to show; sources no
    # longer registered (renamed or removed) come last
    @staticmethod
    def listing_rank(source_name: str) -> int:
        try:
            return job_source_registry.get(source_name).listing_rank
        except UnknownRegistryEntryError:
            return UNRANKED_LISTING

    @staticmethod
    def source_class(source_name: str) -> type[JobSource]:
        return job_source_registry.get(source_name)

    # how the sheet names a source; one no longer registered keeps its name
    @staticmethod
    def display_name(source_name: str) -> str:
        try:
            return job_source_registry.get(source_name).display_name
        except UnknownRegistryEntryError:
            return source_name

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
        return sorted(sources, key=lambda source: (source.reads_tracked_companies, source.reads_stored_jobs))
