from dataclasses import dataclass

from job_hunt.common.settings import settings as app_settings
from job_hunt.common.settings_schema import Settings
from job_hunt.job_pipeline.field_recheck import FieldRechecker
from job_hunt.job_pipeline.kind_refinement import KindRefiner
from job_hunt.job_pipeline.live_listings import live_listings
from job_hunt.job_pipeline.llm_screening import LlmScreening, ScreeningRunReport
from job_hunt.job_pipeline.role_length import RoleLengthReader, RoleLengthReport
from job_hunt.job_pipeline.second_look import SecondLookReport
from job_hunt.job_pipeline.sponsor_status import SponsorStatusLookup
from job_hunt.job_pipeline.title_rules import TitleRules
from job_hunt.job_pipeline.title_screening import TitleScreener, TitleScreeningReport
from job_hunt.job_store.job_repository import JobRepository
from job_hunt.job_store.models import utc_now
from job_hunt.job_store.screening_repository import ScreeningRepository
from job_hunt.job_store.sponsor_repository import SponsorRepository
from job_hunt.job_store.title_screening_repository import TitleScreeningRepository
from job_hunt.llm.llm_client_factory import LlmClientFactory


@dataclass(frozen=True)
class FullScreeningReport:
    titles: TitleScreeningReport
    adverts: ScreeningRunReport
    kinds: SecondLookReport
    fields: SecondLookReport
    lengths: RoleLengthReport

    @property
    def succeeded(self) -> bool:
        return not (
            self.titles.failed_batches
            or self.titles.stopped_by_usage_limit
            or self.adverts.failed_batches
            or self.adverts.stopped_by_usage_limit
            or self.kinds.failed_batches
            or self.kinds.stopped_by_usage_limit
            or self.fields.failed_batches
            or self.fields.stopped_by_usage_limit
            or self.lengths.failed_batches
            or self.lengths.stopped_by_usage_limit
        )

    def summary(self) -> str:
        return (
            f"titles: {self.titles.titles_asked} asked, {self.titles.unrelated} unrelated"
            + (f", {self.titles.failed_batches} batches failed" if self.titles.failed_batches else "")
            + f"; adverts: {self.adverts.summary()}"
            + f"; kind refined: {self.kinds.asked}; unclear field rechecked: {self.fields.asked}"
            + f"; role length: {self.lengths.asked} asked, "
            + f"{self.lengths.ending_in_time} end before the visa does"
        )


# The LLM passes over today's live listings: titles the rules can't sort
# (cheap, many per call), the adverts of the listings still kept, then role
# length only where sponsorship would decide the verdict.
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
    screening_repository = ScreeningRepository()
    advert_report = LlmScreening(settings.llm_screening, llm_client, screening_repository, rules).screen(kept)
    # studentships, fellowships and freelance gigs told apart
    kind_report = KindRefiner(settings.llm_screening, llm_client, screening_repository).refine(kept)
    # To check holds only roles that could be the candidate's
    field_report = FieldRechecker(settings.llm_screening, llm_client, screening_repository).recheck(kept)
    # only where sponsorship would decide the verdict: does the role end in time?
    length_report = RoleLengthReader(settings.llm_screening, llm_client, screening_repository).read(
        kept, SponsorStatusLookup(SponsorRepository())
    )
    return FullScreeningReport(title_report, advert_report, kind_report, field_report, length_report)
