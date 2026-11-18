"""A second look at listings whose field the advert read couldn't tell, so
To check holds only roles that could be the candidate's. Thin listings are
the usual case: a Gradcracker card has only a title and a broad list of
disciplines ("Computing, Data, Maths ...").

The stronger model answers one question: could the role's main work be one
of the candidate's fields? It can only take a listing off To check (a
finance office, operations, a language role) or confirm it may be one; it
never makes a listing valid, so a relevant role can't be lost to a guess.
Each listing is asked once: a "plausible" answer is kept as its reason."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from job_hunt.common.settings_schema import LlmScreeningSettings
from job_hunt.job_pipeline.listings import Listing
from job_hunt.job_pipeline.llm_screening import PLAUSIBLE_FIELD_REASON, UNCLEAR_FIELD_REASON, UNRELATED_REASON
from job_hunt.job_pipeline.second_look import SecondLookReport, ask_about_listings
from job_hunt.job_store.screening_repository import ScreeningRepository
from job_hunt.llm.llm_client import LlmClient

FIELD_PROMPT_TEMPLATE = (
    "Each listing gives little or no advert text. For each, from its title, employer and whatever text "
    "there is: could the role's main day-to-day work be one of: {relevant_fields}?\n"
    "'plausible' only if a data scientist would reasonably expect the job to be mainly data, analytics, "
    "machine learning or AI work, or a technology scheme that may place them in it (e.g. 'Technology "
    "Graduate Programme', 'Digital & Data Analyst', 'Graduate Analyst' at a tech or data firm, "
    "customer, marketing or product analytics or intelligence, a placement whose title names data, AI "
    "or analysis).\n"
    "'no' if the main work is plainly something else, even when the employer or the listed disciplines "
    "mention data: quantitative finance (quant analyst, researcher, developer, trader or strats), "
    "trading, risk management, finance, CFO/COO office, audit, operations, delivery, HR or 'people "
    "science', sales, marketing, legal, construction, engineering disciplines, business improvement, "
    "customer service, roles defined by a language ('Vietnamese speakers'), and events (insight weeks, "
    "open days, information sessions).\n"
    "field: the role's field in a few words."
)


class FieldAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="the listing id exactly as given")
    field: str = Field(min_length=2, description="a few words")
    could_be_relevant: Literal["plausible", "no"]


class FieldReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    listings: list[FieldAnswer]


class FieldRechecker:
    def __init__(
        self, screening_settings: LlmScreeningSettings, llm_client: LlmClient, repository: ScreeningRepository
    ):
        self._settings = screening_settings
        self._llm_client = llm_client
        self._repository = repository
        self._prompt = FIELD_PROMPT_TEMPLATE.format(
            relevant_fields=", ".join(screening_settings.relevant_fields)
        )

    def recheck(self, listings: list[Listing]) -> SecondLookReport:
        report = SecondLookReport()
        stored = self._repository.find(listing.key for listing in listings)
        due = [
            listing
            for listing in listings
            if listing.key in stored and stored[listing.key].reason == UNCLEAR_FIELD_REASON
        ]
        answers = ask_about_listings(
            due,
            lambda reply: reply.listings,
            FieldReply,
            self._prompt,
            self._llm_client,
            self._settings,
            report,
            "field recheck",
        )
        for listing, answer in answers:
            screening = stored[listing.key]
            # answered by the stronger model: it needs no second look
            screening.model = self._settings.second_pass_model
            if answer.could_be_relevant == "no":
                screening.fit = "skip"
                screening.reason = f"{UNRELATED_REASON} ({answer.field})"
            else:
                screening.reason = PLAUSIBLE_FIELD_REASON
            self._repository.save([screening])
        return report
