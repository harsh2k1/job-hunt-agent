from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, TypeVar

from job_hunt.common.registry import Registry
from job_hunt.common.settings_schema import Settings

search_provider_registry: Registry[type["SearchProvider"]] = Registry("search provider")


class SearchProviderError(RuntimeError):
    """A search couldn't be run (rate limit, timeout, provider down)."""


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str


class SearchProvider(ABC):
    """A web search engine, behind one interface so it can be swapped in
    settings (company_discovery.search_provider). Only needs plain keyword
    and site: queries; anything engine-specific stays inside the provider."""

    # registered name; also the name of its settings block
    name: ClassVar[str]

    @classmethod
    @abstractmethod
    def from_settings(cls, settings: Settings) -> "SearchProvider": ...

    @abstractmethod
    def search(self, query: str, max_results: int) -> list[SearchResult]: ...


SearchProviderClassT = TypeVar("SearchProviderClassT", bound=type[SearchProvider])


def register_search_provider(provider_class: SearchProviderClassT) -> SearchProviderClassT:
    search_provider_registry.register(provider_class.name)(provider_class)
    return provider_class
