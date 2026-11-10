import pytest

from job_hunt.common.settings import settings
from job_hunt.job_sources.location_matching import TargetLocationMatcher, join_locations

matcher = TargetLocationMatcher(["london", "united kingdom", "uk", "england"])


@pytest.mark.parametrize(
    "location",
    ["London", "London, England, United Kingdom", "Remote - UK", "LONDON (Hybrid)", "Cambridge, UK"],
)
def test_target_locations_match(location):
    assert matcher.matches(location)


@pytest.mark.parametrize("location", ["Kyiv, Ukraine", "San Francisco, CA", "Paris, France"])
def test_other_locations_dont_match(location):
    assert not matcher.matches(location)


def test_posting_matches_if_any_of_its_locations_does():
    assert matcher.matches_any(["New York", "London"])
    assert not matcher.matches_any(["New York", "Paris"])


def test_posting_without_any_location_is_kept_for_checking():
    assert matcher.matches_any([])
    assert matcher.matches_any([None, ""])


def test_matcher_needs_at_least_one_keyword():
    with pytest.raises(ValueError):
        TargetLocationMatcher(["  "])


def test_joined_locations_drop_blanks_and_repeats_but_keep_order():
    assert join_locations(["London", None, " London ", "Remote - UK", ""]) == "London; Remote - UK"
    assert join_locations([None, ""]) is None


@pytest.mark.parametrize(
    "location, is_target",
    [
        ("Edinburgh", True),
        ("Manchester, Greater Manchester", True),
        ("Remote (UK)", True),
        ("Cardiff, Wales", True),
        ("New York, NY", False),
        ("Reading the room is part of the job", False),
        ("Kyiv, Ukraine", False),
    ],
)
def test_committed_keywords_cover_the_uk_and_avoid_lookalikes(location, is_target):
    matcher = TargetLocationMatcher(settings.job_search.target_location_keywords)
    assert matcher.matches(location) is is_target
