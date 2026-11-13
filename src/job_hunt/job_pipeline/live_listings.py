from dataclasses import dataclass
from datetime import datetime, timedelta

from job_hunt.common.settings_schema import Settings
from job_hunt.job_pipeline.listings import Listing, ListingBuilder
from job_hunt.job_pipeline.title_screening import title_key, title_question
from job_hunt.job_sources.location_matching import TargetLocationMatcher
from job_hunt.job_store.job_repository import JobRepository
from job_hunt.job_store.title_screening_repository import TitleScreeningRepository


@dataclass(frozen=True)
class LiveListings:
    # still open, and not ruled out by title (titles not asked about yet stay)
    kept: list[Listing]
    # with the reason
    skipped: list[tuple[Listing, str]]


# The listings still advertised (a source saw them within the sheet's
# window), minus closed ones and those whose title the LLM found unrelated:
# what both the advert screening and the sheet work from, so they agree.
def live_listings(
    settings: Settings,
    job_repository: JobRepository,
    title_repository: TitleScreeningRepository,
    now: datetime,
) -> LiveListings:
    builder = ListingBuilder(TargetLocationMatcher(settings.job_search.target_location_keywords))
    listings = builder.build(
        job_repository.list_jobs_seen_since(now - timedelta(days=settings.google_sheet.listing_days))
    )
    _, question_hash = title_question(settings.llm_screening)
    related = title_repository.find((title_key(listing.primary.title) for listing in listings), question_hash)
    kept: list[Listing] = []
    skipped: list[tuple[Listing, str]] = []
    for listing in listings:
        if listing.closes_at is not None and listing.closes_at < now:
            skipped.append((listing, "Closing date has passed"))
        elif related.get(title_key(listing.primary.title)) is False:
            skipped.append((listing, "Not a related role (by title)"))
        else:
            kept.append(listing)
    return LiveListings(kept, skipped)
