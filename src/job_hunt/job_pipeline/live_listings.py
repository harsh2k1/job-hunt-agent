from dataclasses import dataclass
from datetime import datetime, timedelta

from job_hunt.common.settings_schema import Settings
from job_hunt.job_pipeline.listings import Listing, ListingBuilder
from job_hunt.job_pipeline.rule_screening import RuleScreening
from job_hunt.job_sources.location_matching import TargetLocationMatcher
from job_hunt.job_store.job_repository import JobRepository


@dataclass(frozen=True)
class RuleScreenedListings:
    kept: list[Listing]
    # with the rule's reason
    skipped: list[tuple[Listing, str]]


# The listings still advertised (a source saw them within the sheet's
# window), through the rule pass: what both the LLM screening and the
# sheet work from, so they always agree.
def rule_screened_live_listings(
    settings: Settings, job_repository: JobRepository, now: datetime
) -> RuleScreenedListings:
    builder = ListingBuilder(TargetLocationMatcher(settings.job_search.target_location_keywords))
    since = now - timedelta(days=settings.google_sheet.listing_days)
    rules = RuleScreening(settings.screening)
    kept: list[Listing] = []
    skipped: list[tuple[Listing, str]] = []
    for listing in builder.build(job_repository.list_jobs_seen_since(since)):
        result = rules.screen(listing, now)
        if result.kept:
            kept.append(listing)
        else:
            skipped.append((listing, result.reason or ""))
    return RuleScreenedListings(kept, skipped)
