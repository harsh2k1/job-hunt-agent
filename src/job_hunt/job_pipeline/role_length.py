"""Whether a role ends before the candidate's Graduate visa does, asked only
where it decides the verdict: a graduate or full-time role that would
otherwise be skipped or flagged for sponsorship. A role that ends in time
(a 12-month programme from September 2027) needs no sponsorship at all."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from job_hunt.common.logger import get_logger
from job_hunt.common.settings_schema import LlmScreeningSettings
from job_hunt.job_pipeline.advert_facts import advert_digest
from job_hunt.job_pipeline.eligibility import sponsorship_rule
from job_hunt.job_pipeline.listings import Listing
from job_hunt.job_store.models import ListingScreening
from job_hunt.job_store.screening_repository import ScreeningRepository
from job_hunt.llm.llm_client import LlmClient, LlmError, LlmUsageLimitError

logger = get_logger(__name__)

LENGTH_PROMPT_TEMPLATE = (
    "For each job listing, from its text only: does the role or programme end on or before "
    "{visa_ends}, if the candidate starts in {start}? 'by' if it does (e.g. a 12-month fixed-term role or "
    "programme); 'after' if it runs past it, is permanent or ongoing, or leads into a permanent role (e.g. "
    "a 2-year graduate scheme); 'not stated' if the text doesn't give its length."
)


class RoleEndAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="the listing id exactly as given")
    role_end: Literal["by", "after", "not stated"]


class RoleEndReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    listings: list[RoleEndAnswer]


@dataclass
class RoleLengthReport:
    asked: int = 0
    ending_in_time: int = 0
    failed_batches: int = 0
    stopped_by_usage_limit: bool = False


# graduate / full-time roles whose verdict hangs on sponsorship, and whose
# length hasn't been asked yet
def needs_role_length(screening: ListingScreening, licence: str, visa_ends: str) -> bool:
    if screening.fit == "skip" or screening.role_end is not None:
        return False
    rule = sponsorship_rule(
        screening.kind or "full-time", bool(screening.part_time), screening.sponsorship, licence, visa_ends
    )
    return rule.eligible is not True


class RoleLengthReader:
    def __init__(
        self, screening_settings: LlmScreeningSettings, llm_client: LlmClient, repository: ScreeningRepository
    ):
        self._settings = screening_settings
        self._llm_client = llm_client
        self._repository = repository
        self._prompt = LENGTH_PROMPT_TEMPLATE.format(
            visa_ends=screening_settings.graduate_visa_ends, start=screening_settings.available_full_time_from
        )

    def read(self, listings: list[Listing], licence_of: Callable[[str], str]) -> RoleLengthReport:
        report = RoleLengthReport()
        stored = self._repository.find(listing.key for listing in listings)
        due = [
            listing
            for listing in listings
            if listing.key in stored
            and needs_role_length(
                stored[listing.key],
                licence_of(listing.primary.company_name),
                self._settings.graduate_visa_ends,
            )
        ]
        for start in range(0, len(due), self._settings.batch_size):
            batch = due[start : start + self._settings.batch_size]
            by_id = {str(position): listing for position, listing in enumerate(batch)}
            lines = "\n".join(
                f"[{listing_id}] {listing.primary.title} at {listing.primary.company_name}: "
                + (self._advert(listing) or "(no advert text)").replace("\n", " ")
                for listing_id, listing in by_id.items()
            )
            try:
                reply = self._llm_client.ask(
                    f"Listings:\n{lines}", RoleEndReply, self._prompt, self._settings.first_pass_model
                )
            except LlmUsageLimitError as error:
                logger.warning("role length stopped: %s", error)
                report.stopped_by_usage_limit = True
                break
            except LlmError as error:
                report.failed_batches += 1
                logger.warning("role length batch failed: %s", error)
                continue
            for answer in reply.listings:
                if answer.id not in by_id:
                    continue
                screening = stored[by_id[answer.id].key]
                screening.role_end = answer.role_end
                self._repository.save([screening])
                report.asked += 1
                report.ending_in_time += answer.role_end == "by"
        return report

    def _advert(self, listing: Listing) -> str | None:
        return advert_digest(
            listing.primary.description,
            self._settings.advert_opening_chars,
            self._settings.advert_key_fact_chars,
        )
