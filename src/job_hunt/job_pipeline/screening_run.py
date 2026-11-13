from job_hunt.common.settings import settings as app_settings
from job_hunt.common.settings_schema import Settings
from job_hunt.job_pipeline.live_listings import rule_screened_live_listings
from job_hunt.job_pipeline.llm_screening import LlmScreening, ScreeningRunReport
from job_hunt.job_store.job_repository import JobRepository
from job_hunt.job_store.models import utc_now
from job_hunt.job_store.screening_repository import ScreeningRepository
from job_hunt.llm.llm_client_factory import LlmClientFactory


# The LLM screening of today's rule-kept listings, wired from settings.
def screen_live_listings(settings: Settings = app_settings) -> ScreeningRunReport:
    screening_settings = settings.llm_screening
    live = rule_screened_live_listings(settings, JobRepository(), utc_now())
    screening = LlmScreening(screening_settings, LlmClientFactory.create(settings.llm), ScreeningRepository())
    return screening.screen(live.kept)
