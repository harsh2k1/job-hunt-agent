from dataclasses import dataclass

from job_hunt.common.settings import settings as app_settings
from job_hunt.common.settings_schema import Settings
from job_hunt.job_pipeline.live_listings import live_listings
from job_hunt.job_pipeline.llm_screening import LlmScreening, ScreeningRunReport
from job_hunt.job_pipeline.title_rules import TitleRules
from job_hunt.job_pipeline.title_screening import TitleScreener, TitleScreeningReport
from job_hunt.job_store.job_repository import JobRepository
from job_hunt.job_store.models import utc_now
from job_hunt.job_store.screening_repository import ScreeningRepository
from job_hunt.job_store.title_screening_repository import TitleScreeningRepository
from job_hunt.llm.llm_client_factory import LlmClientFactory


@dataclass(frozen=True)
class FullScreeningReport:
    titles: TitleScreeningReport
    adverts: ScreeningRunReport

    @property
    def succeeded(self) -> bool:
        return not (
            self.titles.failed_batches
            or self.titles.stopped_by_usage_limit
            or self.adverts.failed_batches
            or self.adverts.stopped_by_usage_limit
        )

    def summary(self) -> str:
        return (
            f"titles: {self.titles.titles_asked} asked, {self.titles.unrelated} unrelated"
            + (f", {self.titles.failed_batches} batches failed" if self.titles.failed_batches else "")
            + f"; adverts: {self.adverts.summary()}"
        )


# Both LLM passes over today's live listings: titles first (cheap, many per
# call), then the adverts of the listings still kept.
def screen_live_listings(settings: Settings = app_settings) -> FullScreeningReport:
    llm_client = LlmClientFactory.create(settings.llm)
    job_repository, title_repository = JobRepository(), TitleScreeningRepository()
    now = utc_now()
    unscreened = live_listings(settings, job_repository, title_repository, now)
    # only titles the free rules can't sort go to the LLM
    rules = TitleRules(settings.title_rules)
    title_report = TitleScreener(settings.llm_screening, llm_client, title_repository).screen(
        [listing.primary.title for listing in unscreened.kept if rules.rule(listing.primary.title) is None]
    )
    kept = live_listings(settings, job_repository, title_repository, now).kept
    advert_report = LlmScreening(settings.llm_screening, llm_client, ScreeningRepository()).screen(kept)
    return FullScreeningReport(title_report, advert_report)
