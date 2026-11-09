import pytest

from common.registry import Registry, UnknownRegistryEntryError


def test_registered_entry_is_returned_by_name():
    registry: Registry[type] = Registry("widget")

    @registry.register("round")
    class RoundWidget:
        pass

    assert registry.get("round") is RoundWidget
    assert registry.names() == ["round"]


def test_registering_a_name_twice_is_an_error():
    registry: Registry[type] = Registry("widget")
    registry.register("round")(type("RoundWidget", (), {}))
    with pytest.raises(ValueError, match="already registered"):
        registry.register("round")(type("OtherRoundWidget", (), {}))


def test_unknown_name_error_says_what_kind_and_what_exists():
    registry: Registry[type] = Registry("widget")
    with pytest.raises(UnknownRegistryEntryError, match="unknown widget 'square'; registered: none"):
        registry.get("square")
