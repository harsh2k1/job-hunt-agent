"""A second look at listings the advert read filed as "other" (studentships,
fellowships, freelance gigs), so the rules can tell them apart: freelance
or contract work isn't open to someone on a student visa, a studentship
isn't a job, and an employed fellowship is treated like any full-time role.
Only "other" listings are asked about, and the answer replaces the kind;
they're few, so the stronger model answers.

Such work is often expert work for training AI (a lawyer or an HR partner
"AI trainer"), which the advert read takes for an AI role; here its field
is judged by the expertise asked for, and an unrelated one isn't shown."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from job_hunt.common.settings_schema import LlmScreeningSettings
from job_hunt.job_pipeline.listings import Listing
from job_hunt.job_pipeline.llm_screening import UNRELATED_REASON
from job_hunt.job_pipeline.second_look import SecondLookReport, ask_about_listings
from job_hunt.job_store.screening_repository import ScreeningRepository
from job_hunt.llm.llm_client import LlmClient

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

    def refine(self, listings: list[Listing]) -> SecondLookReport:
        report = SecondLookReport()
        stored = self._repository.find(listing.key for listing in listings)
        due = [
            listing for listing in listings if listing.key in stored and stored[listing.key].kind == "other"
        ]
        answers = ask_about_listings(
            due,
            lambda reply: reply.listings,
            KindReply,
            self._prompt,
            self._llm_client,
            self._settings,
            report,
            "kind refinement",
        )
        for listing, answer in answers:
            screening = stored[listing.key]
            screening.kind = REFINED_KIND[answer.engagement]
            # answered by the stronger model: it needs no second look
            screening.model = self._settings.second_pass_model
            if not answer.related:
                screening.fit = "skip"
                screening.reason = f"{UNRELATED_REASON} ({answer.expertise})"
            self._repository.save([screening])
        return report
