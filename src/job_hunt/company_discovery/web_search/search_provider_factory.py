from job_hunt.common.settings import settings as app_settings
from job_hunt.common.settings_schema import Settings

# imported so every provider registers itself before the factory is used
import job_hunt.company_discovery.web_search.ddgs_provider  # noqa: F401  # isort: skip
from job_hunt.company_discovery.web_search.search_provider import SearchProvider, search_provider_registry


class SearchProviderFactory:
    @staticmethod
    def create(settings: Settings = app_settings) -> SearchProvider:
        provider_class = search_provider_registry.get(settings.company_discovery.search_provider)
        return provider_class.from_settings(settings)
