"""A second look at listings the advert read filed as "other" (studentships,
fellowships, freelance gigs), so the rules can tell them apart: freelance
or contract work isn't open to someone on a student visa, a studentship
isn't a job, and an employed fellowship is treated like any full-time role.
Only "other" listings are asked about, and the answer replaces the kind;
they're few, so the stronger model answers.

Such work is often expert work for training AI (a lawyer or an HR partner
"AI trainer"), which the advert read takes for an AI role; here its field
is judged by the expertise asked for, and an unrelated one isn't shown."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from job_hunt.common.logger import get_logger
from job_hunt.common.settings_schema import LlmScreeningSettings
from job_hunt.job_pipeline.advert_facts import advert_digest
from job_hunt.job_pipeline.listings import Listing
from job_hunt.job_pipeline.llm_screening import UNRELATED_REASON
from job_hunt.job_store.screening_repository import ScreeningRepository
from job_hunt.llm.llm_client import LlmClient, LlmError, LlmUsageLimitError

logger = get_logger(__name__)

KIND_PROMPT_TEMPLATE = (
    "For each listing, from its text only:\n"
    "engagement: 'freelance' = self-employed, freelance, contractor, gig or paid-per-task work (e.g. AI "
    "training or annotation on a platform); 'studentship' = a PhD or other studentship, scholarship or "
    "grant; 'employed' = a salaried job, including a fellowship or fixed-term employment.\n"
    "expertise: the expertise the work itself asks for, in a few words (for AI-training work, the "
    "subject the trainer must know, e.g. 'corporate law', not 'AI').\n"
    "related: true if that expertise is one of: {relevant_fields}. With no advert text, judge both "
    "from the title."
)
REFINED_KIND = {"freelance": "freelance", "studentship": "studentship", "employed": "full-time"}


class KindAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="the listing id exactly as given")
    engagement: Literal["freelance", "studentship", "employed"]
    # never empty: a blank answer can't justify hiding a listing
    expertise: str = Field(min_length=2, description="a few words")
    related: bool


class KindReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    listings: list[KindAnswer]


@dataclass
class KindRefinementReport:
    asked: int = 0
    failed_batches: int = 0
    stopped_by_usage_limit: bool = False


class KindRefiner:
    def __init__(
        self, screening_settings: LlmScreeningSettings, llm_client: LlmClient, repository: ScreeningRepository
    ):
        self._settings = screening_settings
        self._llm_client = llm_client
        self._repository = repository
        self._prompt = KIND_PROMPT_TEMPLATE.format(
            relevant_fields=", ".join(screening_settings.relevant_fields)
        )

    def refine(self, listings: list[Listing]) -> KindRefinementReport:
        report = KindRefinementReport()
        stored = self._repository.find(listing.key for listing in listings)
        due = [
            listing for listing in listings if listing.key in stored and stored[listing.key].kind == "other"
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
                    f"Listings:\n{lines}", KindReply, self._prompt, self._settings.second_pass_model
                )
            except LlmUsageLimitError as error:
                logger.warning("kind refinement stopped: %s", error)
                report.stopped_by_usage_limit = True
                break
            except LlmError as error:
                report.failed_batches += 1
                logger.warning("kind refinement batch failed: %s", error)
                continue
            for answer in reply.listings:
                if answer.id in by_id:
                    screening = stored[by_id[answer.id].key]
                    screening.kind = REFINED_KIND[answer.engagement]
                    if not answer.related:
                        screening.fit = "skip"
                        screening.reason = f"{UNRELATED_REASON} ({answer.expertise})"
                    self._repository.save([screening])
                    report.asked += 1
        return report

    def _advert(self, listing: Listing) -> str | None:
        return advert_digest(
            listing.primary.description,
            self._settings.advert_opening_chars,
            self._settings.advert_key_fact_chars,
        )
