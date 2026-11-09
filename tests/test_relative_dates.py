from datetime import datetime, timedelta, timezone

import pytest

from job_hunt.job_sources.relative_dates import parse_relative_age

NOW = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "text, age",
    [
        ("3 days ago", timedelta(days=3)),
        ("an hour ago", timedelta(hours=1)),
        ("30+ days ago", timedelta(days=30)),
        ("2 weeks ago", timedelta(weeks=2)),
        ("Posted 1 month ago", timedelta(days=30)),
    ],
)
def test_relative_ages_are_read(text, age):
    assert parse_relative_age(text, NOW) == NOW - age


@pytest.mark.parametrize("text", [None, "", "Just posted", "Full-time"])
def test_unreadable_ages_give_none(text):
    assert parse_relative_age(text, NOW) is None
