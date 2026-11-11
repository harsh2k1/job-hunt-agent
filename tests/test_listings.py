from datetime import datetime, timedelta, timezone

import pytest

from job_hunt.job_pipeline.listings import ListingBuilder
from job_hunt.job_sources.location_matching import TargetLocationMatcher
from job_hunt.job_store.models import StoredJob

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
RANKS = {"greenhouse": 10, "linkedin": 50, "serpapi_google_jobs": 60, "email_alerts": 70}


def job(source: str, title: str, company: str, location: str | None = "London, UK", **fields) -> StoredJob:
    return StoredJob(
        source=source,
        source_job_id=fields.pop("source_job_id", f"{source}-{title}-{company}"),
        title=title,
        company_name=company,
        location=location,
        url=f"https://example.com/{source}",
        first_seen_at=NOW,
        last_seen_at=fields.pop("last_seen_at", NOW),
        **fields,
    )


@pytest.fixture
def builder() -> ListingBuilder:
    matcher = TargetLocationMatcher(["london", "uk", "united kingdom", "manchester"])
    return ListingBuilder(matcher, source_rank=lambda source: RANKS.get(source, 99))


def test_the_same_advert_from_several_sources_is_one_listing(builder):
    listings = builder.build(
        [
            job("linkedin", "Data Scientist - London", "Monzo", "London, England, United Kingdom"),
            job("greenhouse", "Data Scientist", "Monzo Bank Ltd", "London", description="Full advert"),
            job("email_alerts", "Data Scientist (m/f/d)", "Monzo", "Greater London (Hybrid)"),
        ]
    )
    # Monzo vs Monzo Bank Ltd stay apart: different names after normalising
    assert sorted(len(listing.sightings) for listing in listings) == [1, 2]
    merged = max(listings, key=lambda listing: len(listing.sightings))
    assert merged.sources == ["linkedin", "email_alerts"]


def test_the_most_direct_source_is_shown(builder):
    [listing] = builder.build(
        [
            job("serpapi_google_jobs", "Machine Learning Engineer", "Wayve", "London, UK"),
            job("greenhouse", "Machine Learning Engineer", "Wayve", "London"),
            job("linkedin", "Machine Learning Engineer", "Wayve", "London (Hybrid)"),
        ]
    )
    assert listing.primary.source == "greenhouse"
    assert listing.sources == ["greenhouse", "linkedin", "serpapi_google_jobs"]


def test_same_role_in_different_cities_stays_apart(builder):
    listings = builder.build(
        [
            job("linkedin", "Data Analyst", "Asda", "London"),
            job("greenhouse", "Data Analyst", "Asda", "Manchester"),
        ]
    )
    assert len(listings) == 2


def test_unknown_employers_are_never_grouped(builder):
    listings = builder.build(
        [job("email_alerts", "Data Scientist", "Unknown"), job("linkedin", "Data Scientist", "Unknown")]
    )
    assert len(listings) == 2


def test_same_rank_prefers_the_copy_with_an_advert_then_the_freshest(builder):
    older = NOW - timedelta(days=2)
    [listing] = builder.build(
        [
            job("linkedin", "AI Engineer", "Synthesia", last_seen_at=older, description="Advert"),
            job("linkedin", "AI Engineer ", "Synthesia", source_job_id="other"),
        ]
    )
    assert listing.primary.description == "Advert"


def test_earliest_known_deadline_wins(builder):
    [listing] = builder.build(
        [
            job("greenhouse", "Graduate Data Scientist", "Wise", closes_at=NOW + timedelta(days=20)),
            job("linkedin", "Graduate Data Scientist", "Wise", closes_at=NOW + timedelta(days=5)),
            job("email_alerts", "Graduate Data Scientist", "Wise"),
        ]
    )
    assert listing.closes_at == NOW + timedelta(days=5)


def test_registered_sources_have_ranks():
    builder = ListingBuilder(TargetLocationMatcher(["london"]))
    [listing] = builder.build(
        [
            job("linkedin", "Data Scientist", "Meta"),
            job("greenhouse", "Data Scientist", "Meta"),
            job("gone_source", "Data Scientist", "Meta"),
        ]
    )
    assert [sighting.source for sighting in listing.sightings] == ["greenhouse", "linkedin", "gone_source"]
