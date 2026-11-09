from collections.abc import Callable
from typing import Any, TypeVar

from common.cache.backends import CACHE_MISS, CacheBackend

ComputedT = TypeVar("ComputedT")

SECONDS_PER_HOUR = 3600


class Cache:
    """What the rest of the code uses to cache things, whatever the backend.

    Adds the key prefix and the default time-to-live, so backends only store
    and fetch.
    """

    def __init__(self, backend: CacheBackend, key_prefix: str, default_ttl_hours: float):
        self._backend = backend
        self._key_prefix = key_prefix
        self._default_ttl_hours = default_ttl_hours

    def get(self, key: str, default: Any = None) -> Any:
        value = self._backend.get(self._namespaced(key))
        return default if value is CACHE_MISS else value

    def set(self, key: str, value: Any, ttl_hours: float | None = None) -> None:
        self._backend.set(self._namespaced(key), value, self._ttl_seconds(ttl_hours))

    # The usual way in: return the cached value, or compute, store and return it.
    def get_or_compute(
        self, key: str, compute: Callable[[], ComputedT], ttl_hours: float | None = None
    ) -> ComputedT:
        namespaced_key = self._namespaced(key)
        cached_value = self._backend.get(namespaced_key)
        if cached_value is not CACHE_MISS:
            return cached_value
        computed_value = compute()
        self._backend.set(namespaced_key, computed_value, self._ttl_seconds(ttl_hours))
        return computed_value

    def delete(self, key: str) -> None:
        self._backend.delete(self._namespaced(key))

    # drops everything in the backend, not only this prefix
    def clear(self) -> None:
        self._backend.clear()

    def close(self) -> None:
        self._backend.close()

    def _namespaced(self, key: str) -> str:
        return f"{self._key_prefix}:{key}"

    def _ttl_seconds(self, ttl_hours: float | None) -> float:
        hours = self._default_ttl_hours if ttl_hours is None else ttl_hours
        return hours * SECONDS_PER_HOUR
