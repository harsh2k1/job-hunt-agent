"""The browser part runs against the live site; these cover reading what a
result card shows, from cards as rendered in Nov 2026."""

from datetime import datetime, timezone

import pytest

from job_hunt.common.settings import settings
from job_hunt.job_sources.location_matching import TargetLocationMatcher
from job_hunt.job_sources.portals.kcl_target_connect import (
    KclTargetConnectSource,
    TargetConnectCard,
    closing_date,
    is_signed_in,
    read_spec,
)

TODAY = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)

CARD = TargetConnectCard(
    job_id="82705",
    title="Graduate Data Scientist",
    organisation="South London & Maudsley NHS Foundation Trust",
    spec="Graduate Employment | UK - London | £45,000 - £49,999",
    days="52 days to apply",
)


def make_source() -> KclTargetConnectSource:
    return KclTargetConnectSource(
        settings.job_sources.kcl_target_connect,
        None,
        TargetLocationMatcher(settings.job_search.target_location_keywords),
    )


@pytest.mark.parametrize(
    "spec, expected",
    [
        (
            "Graduate Employment | UK - London | £45,000 - £49,999",
            ("Graduate Employment", "UK - London", "£45,000 - £49,999"),
        ),
        (
            "Law Vacation Scheme | UK - London +6 | National Minimum or Living Wage",
            (
                "Law Vacation Scheme",
                "UK - London +6",
                "National Minimum or Living Wage",
            ),
        ),
        ("Internship", ("Internship", None, None)),
        ("", (None, None, None)),
    ],
)
def test_card_spec_is_split_into_its_parts(spec, expected):
    parts = read_spec(spec)
    assert (parts.employment_type, parts.location, parts.salary) == expected


@pytest.mark.parametrize(
    "days, expected",
    [
        ("52 days to apply", datetime(2027, 1, 1, 23, 59, tzinfo=timezone.utc)),
        ("1 day to apply", datetime(2026, 11, 11, 23, 59, tzinfo=timezone.utc)),
        ("Last day to apply", datetime(2026, 11, 10, 23, 59, tzinfo=timezone.utc)),
        ("Rolling deadline", None),
    ],
)
def test_days_to_apply_become_a_closing_date(days, expected):
    assert closing_date(days, TODAY) == expected


def test_card_becomes_a_job():
    job = make_source()._to_fetched_job(CARD)
    assert job.source_job_id == "82705"
    assert job.url == "https://kcl.targetconnect.net/student/jobs.html?id=82705"
    assert (job.location, job.employment_type, job.salary_text) == (
        "UK - London",
        "Graduate Employment",
        "£45,000 - £49,999",
    )
    assert job.closes_at is not None


def test_cards_outside_the_uk_are_dropped():
    overseas = TargetConnectCard(
        "1", "Analyst", "Acme", "Graduate Employment | Overseas - USA | £", "5 days to apply"
    )
    source = make_source()
    assert source._is_in_scope(CARD)
    assert not source._is_in_scope(overseas)


@pytest.mark.parametrize(
    "url, signed_in",
    [
        ("https://kcl.targetconnect.net/student/home.html", True),
        ("https://kcl.targetconnect.net/unauth/student/login", False),
        ("https://login.microsoftonline.com/abc/oauth2", False),
        ("https://kcl.targetconnect.net.evil.example/student", False),
    ],
)
def test_signed_in_means_a_target_connect_page_past_the_login(url, signed_in):
    assert is_signed_in(url) is signed_in
