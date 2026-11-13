"""LLM screening of the listings the rule pass keeps.

The model only reads facts out of each advert: the role's field, its kind,
part-time or not, whether it starts once the candidate is free, sponsorship
and the start date. Whether a listing stays is then decided in code, on two
grounds only: the role isn't related to the candidate's fields at all, or
it's full-time and doesn't start once the candidate is free. No judgement
of fit, level or experience: the candidate decides that.

Token use is kept down: only listings without a current verdict are sent,
in batches, as a digest of the advert (its opening plus every line about
visas, start dates and hours). The cheap model reads everything; the
stronger one only re-reads adverts whose field it couldn't tell. Verdicts
are stored, so a listing is sent again only when its advert changes.
"""

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from job_hunt.common.logger import get_logger
from job_hunt.common.settings_schema import LlmScreeningSettings
from job_hunt.job_pipeline.advert_facts import advert_digest, sponsorship_in_advert
from job_hunt.job_pipeline.eligibility import RoleKind, StartsWhenFree, eligibility
from job_hunt.job_pipeline.listings import Listing
from job_hunt.job_store.models import ListingScreening, utc_now
from job_hunt.job_store.screening_repository import ScreeningRepository
from job_hunt.llm.llm_client import LlmClient, LlmError, LlmUsageLimitError

logger = get_logger(__name__)

# part of every content hash: a change to what's asked re-screens everything
PROMPT_VERSION = "6"

APPLY, CHECK, SKIP = "apply", "check", "skip"

SYSTEM_PROMPT_TEMPLATE = (
    "You read UK job listings and report facts from each listing's text only. Never judge whether "
    "the candidate is a good fit, and never consider experience or seniority. Be brief.\n"
    "role_field: the role's field in a few words, e.g. 'data science', 'ML engineering', 'software "
    "engineering', 'sales'.\n"
    "related: 'yes' if the role's day-to-day work involves any of: {relevant_fields}; 'no' only if it "
    "plainly involves none of them (e.g. general software engineering, sales, retail, operations); "
    "'unclear' if the text is too thin to tell. When in doubt, 'yes'.\n"
    "kind: graduate = a graduate scheme or programme, or a role advertised for new / recent / upcoming "
    "graduates or early careers; internship = an internship, placement, spring week or insight programme; "
    "part-time = a part-time job that isn't an internship; full-time = any other permanent or fixed-term "
    "full-time job, including a plain job title with no advert text; other = only studentships, "
    "fellowships, freelance or contract gigs the text names as such.\n"
    "part_time: true only if the role is part-time or at most about 20 hours a week.\n"
    "starts_when_free: the candidate is free full-time from {available_from}. 'yes' if the advert's start "
    "is then or later (a 2027 graduate intake usually starts in September 2027), 'no' if it's earlier or "
    "as soon as possible, 'not stated' if the advert doesn't say.\n"
    "sponsorship: 'offered' or 'not offered' only if the text says so, else 'not stated'; when "
    "sponsorship_in_text is given, that is what the advert says.\n"
    "start_date: the start as the advert words it, else null."
)


class ListingFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="the listing id exactly as given")
    role_field: str = Field(description="a few words")
    related: Literal["yes", "no", "unclear"]
    kind: RoleKind
    part_time: bool
    starts_when_free: StartsWhenFree
    sponsorship: Literal["offered", "not offered", "not stated"]
    start_date: str | None


class ScreeningReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    listings: list[ListingFacts]


@dataclass(frozen=True)
class Decision:
    verdict: str
    # why it's skipped or to be checked; empty for apply
    reason: str
    # bullets on why it's valid; None unless apply
    valid_because: str | None


# The only two reasons a listing goes: not related at all, or the rule on
# kind and start says the candidate can't take it.
def decide(facts: ListingFacts) -> Decision:
    if facts.related == "no":
        return Decision(SKIP, f"Not a related role ({facts.role_field})", None)
    rule = eligibility(facts.kind, facts.part_time, facts.starts_when_free)
    if rule.eligible is False:
        return Decision(SKIP, f"Not applicable: {rule.reason}", None)
    if facts.related == "unclear":
        return Decision(CHECK, "The advert doesn't say enough to tell the role's field", None)
    if rule.eligible is None:
        return Decision(CHECK, rule.reason[0].upper() + rule.reason[1:], None)
    bullets = [rule.basis, f"{facts.role_field[0].upper()}{facts.role_field[1:]} role"]
    if facts.start_date:
        bullets.append(f"Starts {facts.start_date}")
    return Decision(APPLY, "", "\n".join(f"• {bullet}" for bullet in bullets))


@dataclass
class ScreeningRunReport:
    to_screen: int = 0
    already_screened: int = 0
    screened: int = 0
    sent_to_second_pass: int = 0
    # over this run's cap; screened on a later run
    deferred: int = 0
    failed_batches: int = 0
    stopped_by_usage_limit: bool = False
    verdicts: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        text = (
            f"{self.screened} screened ({self.sent_to_second_pass} to the second pass), "
            f"{self.already_screened} already screened, {self.deferred} left for the next run; "
            + ", ".join(f"{count} {verdict}" for verdict, count in sorted(self.verdicts.items()))
        )
        if self.failed_batches:
            text += f"; {self.failed_batches} batches failed"
        if self.stopped_by_usage_limit:
            text += "; stopped at the plan's usage limit"
        return text


class LlmScreening:
    def __init__(
        self, screening_settings: LlmScreeningSettings, llm_client: LlmClient, repository: ScreeningRepository
    ):
        self._settings = screening_settings
        self._llm_client = llm_client
        self._repository = repository
        self._system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            relevant_fields=", ".join(screening_settings.relevant_fields),
            available_from=screening_settings.available_full_time_from,
        )
        self._prompt_hash = hashlib.sha1(f"{PROMPT_VERSION}|{self._system_prompt}".encode()).hexdigest()

    def screen(self, listings: list[Listing]) -> ScreeningRunReport:
        report = ScreeningRunReport()
        stored = self._repository.find(listing.key for listing in listings)
        due = [listing for listing in listings if self._is_due(listing, stored.get(listing.key))]
        report.already_screened = len(listings) - len(due)
        report.to_screen = len(due)
        batch_listings = due[: self._settings.max_listings_per_run]
        report.deferred = len(due) - len(batch_listings)
        # facts gathered so far survive a usage limit hit midway
        facts: dict[str, tuple[ListingFacts, str]] = {}
        try:
            self._run_pass(batch_listings, self._settings.first_pass_model, report, facts)
            # the stronger model only helps where there's advert text to read
            unclear = [
                listing
                for listing in batch_listings
                if listing.key in facts
                and facts[listing.key][0].related == "unclear"
                and listing.primary.description
            ]
            report.sent_to_second_pass = len(unclear)
            self._run_pass(unclear, self._settings.second_pass_model, report, facts)
        except LlmUsageLimitError as error:
            logger.warning("LLM screening stopped: %s", error)
            report.stopped_by_usage_limit = True
        report.screened = len(facts)
        for listing_facts, _ in facts.values():
            verdict = decide(listing_facts).verdict
            report.verdicts[verdict] = report.verdicts.get(verdict, 0) + 1
        return report

    def _is_due(self, listing: Listing, stored: ListingScreening | None) -> bool:
        return stored is None or stored.content_hash != self._content_hash(listing)

    def _advert(self, listing: Listing) -> str | None:
        return advert_digest(
            listing.primary.description,
            self._settings.advert_opening_chars,
            self._settings.advert_key_fact_chars,
        )

    # what was sent: a different advert or question means a new hash
    def _content_hash(self, listing: Listing) -> str:
        job = listing.primary
        content = "|".join([job.title, job.company_name, job.location or "", self._advert(listing) or ""])
        return hashlib.sha1(f"{self._prompt_hash}|{content}".encode()).hexdigest()

    # adds listing key -> (facts, model) to facts; each batch is stored as
    # soon as it's answered; a failed batch's listings stay due
    def _run_pass(
        self,
        listings: list[Listing],
        model: str,
        report: ScreeningRunReport,
        facts: dict[str, tuple[ListingFacts, str]],
    ) -> None:
        for batch in _chunks(listings, self._settings.batch_size):
            by_id = {str(position): listing for position, listing in enumerate(batch)}
            try:
                reply = self._llm_client.ask(self._prompt(by_id), ScreeningReply, self._system_prompt, model)
            except LlmUsageLimitError:
                raise
            except LlmError as error:
                report.failed_batches += 1
                logger.warning("LLM screening batch failed on %s: %s", model, error)
                continue
            answered = {by_id[item.id].key: (item, model) for item in reply.listings if item.id in by_id}
            self._repository.save(self._screenings(batch, answered))
            facts.update(answered)

    def _prompt(self, by_id: dict[str, Listing]) -> str:
        lines = [
            json.dumps(
                {
                    "id": listing_id,
                    "title": listing.primary.title,
                    "company": listing.primary.company_name,
                    "location": listing.primary.location,
                    "type": listing.primary.employment_type,
                    "closes": listing.closes_at.date().isoformat() if listing.closes_at else None,
                    "advert": self._advert(listing),
                    # read from the whole advert by rule; null when it doesn't say
                    "sponsorship_in_text": sponsorship_in_advert(listing.primary.description),
                },
                ensure_ascii=False,
            )
            for listing_id, listing in by_id.items()
        ]
        return "Listings (one JSON object per line):\n" + "\n".join(lines)

    def _screenings(
        self, listings: list[Listing], facts: dict[str, tuple[ListingFacts, str]]
    ) -> list[ListingScreening]:
        screenings = []
        for listing in listings:
            if listing.key not in facts:
                continue
            listing_facts, model = facts[listing.key]
            decision = decide(listing_facts)
            screenings.append(
                ListingScreening(
                    listing_key=listing.key,
                    content_hash=self._content_hash(listing),
                    fit=decision.verdict,
                    reason=decision.reason,
                    valid_because=decision.valid_because,
                    role_field=listing_facts.role_field,
                    kind=listing_facts.kind,
                    part_time=listing_facts.part_time,
                    starts_when_free=listing_facts.starts_when_free,
                    # the rule reads the whole advert, so where it finds an answer it wins
                    sponsorship=sponsorship_in_advert(listing.primary.description)
                    or listing_facts.sponsorship,
                    start_date=listing_facts.start_date,
                    model=model,
                    screened_at=utc_now(),
                )
            )
        return screenings


def _chunks(listings: list[Listing], size: int) -> Iterator[list[Listing]]:
    for start in range(0, len(listings), size):
        yield listings[start : start + size]
