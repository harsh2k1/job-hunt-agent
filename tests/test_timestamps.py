import pytest

from job_hunt.job_sources.timestamps import parse_iso_timestamp


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2026-11-05T10:22:31Z", "2026-11-05T10:22:31+00:00"),
        ("2026-11-06T00:00:00+01:00", "2026-11-06T00:00:00+01:00"),
        ("2026-10-30", "2026-10-30T00:00:00+00:00"),
    ],
)
def test_iso_forms_used_by_sources(text, expected):
    assert parse_iso_timestamp(text).isoformat() == expected


@pytest.mark.parametrize("text", [None, "", "next Tuesday"])
def test_unreadable_values_are_unknown(text):
    assert parse_iso_timestamp(text) is None
