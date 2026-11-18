"""Which stored jobs the sheet would still show: not ruled out by title (the
free rules, or the LLM) and not judged unrelated from their advert. Used to
spend paid-for or rate-limited work (a web search) only where it counts:
roles bound for Jobs or To check (or not screened yet) come first, related
ones ruled out (Skipped) after."""

from job_hunt.common.settings_schema import Settings
from job_hunt.job_pipeline.listings import ListingBuilder
from job_hunt.job_pipeline.llm_screening import UNRELATED_REASON
from job_hunt.job_pipeline.title_rules import TitleRules
from job_hunt.job_pipeline.title_screening import title_key, title_question
from job_hunt.job_sources.location_matching import TargetLocationMatcher
from job_hunt.job_store.models import StoredJob
from job_hunt.job_store.screening_repository import ScreeningRepository
from job_hunt.job_store.title_screening_repository import TitleScreeningRepository


def cards_the_sheet_would_show(
    settings: Settings,
    jobs: list[StoredJob],
    title_repository: TitleScreeningRepository | None = None,
    screening_repository: ScreeningRepository | None = None,
) -> list[StoredJob]:
    rules = TitleRules(settings.title_rules)
    _, question_hash = title_question(settings.llm_screening)
    title_verdicts = (title_repository or TitleScreeningRepository()).find(
        (title_key(job.title) for job in jobs), question_hash
    )
    builder = ListingBuilder(TargetLocationMatcher(settings.job_search.target_location_keywords))
    keys = {id(job): builder.listing_key(job) or f"{job.source}:{job.source_job_id}" for job in jobs}
    screenings = (screening_repository or ScreeningRepository()).find(keys.values())
    shown, skipped = [], []
    for job in jobs:
        if rules.rule(job.title) == "drop" or title_verdicts.get(title_key(job.title)) is False:
            continue
        screening = screenings.get(keys[id(job)])
        if screening is not None and screening.reason.startswith(UNRELATED_REASON):
            continue
        (skipped if screening is not None and screening.fit == "skip" else shown).append(job)
    return shown + skipped
