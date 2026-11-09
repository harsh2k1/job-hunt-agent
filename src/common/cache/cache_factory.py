from functools import cache

from common.cache.backends import cache_backend_registry
from common.cache.cache import Cache
from common.settings import settings
from common.settings_schema import CacheSettings


class CacheFactory:
    """Builds a Cache on whichever backend the settings name."""

    @staticmethod
    def create(cache_settings: CacheSettings) -> Cache:
        backend_class = cache_backend_registry.get(cache_settings.backend)
        return Cache(
            backend=backend_class.from_settings(cache_settings),
            key_prefix=cache_settings.key_prefix,
            default_ttl_hours=cache_settings.default_ttl_hours,
        )


# the one Cache the app shares; tests build their own through the factory
@cache
def get_cache() -> Cache:
    return CacheFactory.create(settings.cache)
