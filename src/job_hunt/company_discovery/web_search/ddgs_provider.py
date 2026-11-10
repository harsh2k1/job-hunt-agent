from typing import Any

from ddgs import DDGS
from ddgs.exceptions import DDGSException

from job_hunt.common.settings_schema import DdgsSearchSettings, Settings
from job_hunt.company_discovery.web_search.search_provider import (
    SearchProvider,
    SearchProviderError,
    SearchResult,
    register_search_provider,
)


@register_search_provider
class DdgsSearchProvider(SearchProvider):
    """Free web search with no key, through the ddgs library (it queries
    several public engines and merges the results). Results include the odd
    ad; that's fine here, since only links to an ATS board are kept."""

    name = "ddgs"

    def __init__(self, provider_settings: DdgsSearchSettings, client: Any = None):
        self._settings = provider_settings
        self._client = client or DDGS(timeout=provider_settings.timeout_seconds)

    @classmethod
    def from_settings(cls, settings: Settings) -> "DdgsSearchProvider":
        return cls(settings.company_discovery.ddgs)

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        try:
            results = self._client.text(
                query, region=self._settings.region, max_results=max_results, backend=self._settings.backend
            )
        except DDGSException as error:
            # rate limits and timeouts are subclasses; "no results" is one too
            if "no results" in str(error).lower():
                return []
            raise SearchProviderError(f"ddgs search for {query!r} failed: {error}") from error
        return [
            SearchResult(result["href"], result.get("title", "")) for result in results if result.get("href")
        ]
