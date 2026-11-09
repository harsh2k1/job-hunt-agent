import threading
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from typing import Any, Final

import diskcache

from common.registry import Registry
from common.settings_schema import CacheSettings

# Returned by a backend when a key is absent or expired. A sentinel rather than
# None, so a cached None ("this lookup found nothing") still counts as a hit.
CACHE_MISS: Final = object()

cache_backend_registry: Registry[type["CacheBackend"]] = Registry("cache backend")


class CacheBackend(ABC):
    """Where cached values physically live. Keys arrive already namespaced and
    TTLs already resolved; the Cache facade handles both.

    A new store (Redis, say) is one subclass registered under the name used
    for `cache.backend` in settings.yaml.
    """

    @classmethod
    @abstractmethod
    def from_settings(cls, cache_settings: CacheSettings) -> "CacheBackend": ...

    @abstractmethod
    def get(self, key: str) -> Any: ...

    @abstractmethod
    def set(self, key: str, value: Any, ttl_seconds: float) -> None: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def clear(self) -> None: ...

    def close(self) -> None:
        return None


@cache_backend_registry.register("disk")
class DiskCacheBackend(CacheBackend):
    """Entries in a local SQLite-backed store, kept between runs."""

    def __init__(self, store: diskcache.Cache):
        self._store = store

    @classmethod
    def from_settings(cls, cache_settings: CacheSettings) -> "DiskCacheBackend":
        disk = cache_settings.disk
        disk.dir.mkdir(parents=True, exist_ok=True)
        return cls(diskcache.Cache(str(disk.dir), size_limit=disk.size_limit_megabytes * 1024 * 1024))

    def get(self, key: str) -> Any:
        return self._store.get(key, default=CACHE_MISS)

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        self._store.set(key, value, expire=ttl_seconds)

    def delete(self, key: str) -> None:
        self._store.delete(key)

    def clear(self) -> None:
        self._store.clear()

    def close(self) -> None:
        self._store.close()


@cache_backend_registry.register("memory")
class InMemoryCacheBackend(CacheBackend):
    """Entries in a dict for the life of the process. Least recently used
    entries are dropped once max_entries is reached."""

    def __init__(self, max_entries: int):
        self._max_entries = max_entries
        # key -> (value, expiry on the monotonic clock)
        self._entries: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        # fetches may run in threads later; OrderedDict moves aren't atomic
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, cache_settings: CacheSettings) -> "InMemoryCacheBackend":
        return cls(cache_settings.memory.max_entries)

    def get(self, key: str) -> Any:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return CACHE_MISS
            value, expires_at = entry
            if time.monotonic() >= expires_at:
                del self._entries[key]
                return CACHE_MISS
            self._entries.move_to_end(key)
            return value

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        with self._lock:
            self._entries[key] = (value, time.monotonic() + ttl_seconds)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def delete(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
