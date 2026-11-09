import pytest

from common.cache import Cache, CacheFactory
from common.cache.backends import CACHE_MISS, InMemoryCacheBackend, cache_backend_registry
from common.settings import settings
from common.settings_schema import DiskCacheSettings


def test_written_value_reads_back(cache):
    cache.set("greenhouse:wayve", [{"id": 1}])
    assert cache.get("greenhouse:wayve") == [{"id": 1}]


def test_missing_key_reads_as_the_default(cache):
    assert cache.get("never-written") is None
    assert cache.get("never-written", default=[]) == []


def test_cached_none_is_a_hit_not_a_miss(cache):
    calls = []
    cache.set("empty-board", None)
    assert cache.get_or_compute("empty-board", lambda: calls.append(1)) is None
    assert calls == []


def test_get_or_compute_only_computes_once(cache):
    calls = []

    def fetch_board() -> list[str]:
        calls.append(1)
        return ["job-1"]

    assert cache.get_or_compute("board", fetch_board) == ["job-1"]
    assert cache.get_or_compute("board", fetch_board) == ["job-1"]
    assert len(calls) == 1


def test_expired_value_reads_as_missing(cache):
    cache.set("short-lived", "value", ttl_hours=0)
    assert cache.get("short-lived") is None


def test_changing_the_key_prefix_hides_old_entries():
    shared_backend = InMemoryCacheBackend(max_entries=10)
    Cache(shared_backend, key_prefix="v1", default_ttl_hours=1).set("board", "old")
    assert Cache(shared_backend, key_prefix="v2", default_ttl_hours=1).get("board") is None


def test_memory_backend_drops_the_least_recently_used_entry():
    backend = InMemoryCacheBackend(max_entries=2)
    backend.set("a", 1, ttl_seconds=60)
    backend.set("b", 2, ttl_seconds=60)
    backend.get("a")  # a is now more recent than b
    backend.set("c", 3, ttl_seconds=60)

    assert backend.get("b") is CACHE_MISS
    assert (backend.get("a"), backend.get("c")) == (1, 3)


# disk entries outlive the Cache that wrote them (that's the point of it);
# memory entries live and die with their Cache
@pytest.mark.parametrize("backend_name, survives_restart", [("disk", True), ("memory", False)])
def test_factory_builds_the_configured_backend(tmp_path, backend_name, survives_restart):
    cache_settings = settings.cache.model_copy(
        update={"backend": backend_name, "disk": DiskCacheSettings(dir=tmp_path, size_limit_megabytes=1)}
    )
    first_run_cache = CacheFactory.create(cache_settings)
    first_run_cache.set("key", "value")
    assert first_run_cache.get("key") == "value"
    first_run_cache.close()

    second_run_cache = CacheFactory.create(cache_settings)
    assert (second_run_cache.get("key") == "value") is survives_restart
    second_run_cache.close()


def test_both_cache_backends_are_registered():
    assert cache_backend_registry.names() == ["disk", "memory"]
