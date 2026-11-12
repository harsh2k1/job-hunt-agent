"""LLM screening of the listings the rule pass keeps.

Token use is kept down on purpose: only listings without a current verdict
are sent, in batches, with the advert cut short; the cheap model sees them
first and the stronger one only the ones it can't decide that have advert
text to read. Verdicts are stored, so a listing is sent again only when its
advert or the candidate's profile changes.
"""

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from job_hunt.common.logger import get_logger
from job_hunt.common.settings_schema import LlmScreeningSettings
from job_hunt.job_pipeline.advert_facts import advert_digest, sponsorship_in_advert
from job_hunt.job_pipeline.listings import Listing
from job_hunt.job_store.models import ListingScreening, utc_now
from job_hunt.job_store.screening_repository import ScreeningRepository
from job_hunt.llm.llm_client import LlmClient, LlmError, LlmUsageLimitError

logger = get_logger(__name__)

Fit = Literal["strong", "good", "check", "skip"]
# part of every content hash: a change to what's sent re-screens everything
PROMPT_VERSION = "2"
UNDECIDED: Fit = "check"

SYSTEM_PROMPT = (
    "You screen UK job listings for one candidate, inside an automated pipeline. Judge each listing "
    "against the candidate's profile only (experience, availability, right to work) and only on its text; "
    "never invent facts. Be brief.\n"
    "fit: strong = a role the profile wants, at a level the candidate's experience matches; good = "
    "suitable but second choice (an adjacent data role, or a level or timing to confirm); check = can't "
    "tell from the text, or there's a concern to confirm (e.g. a full-time start before the candidate is "
    "free); skip = clearly unsuitable: a different field, not in the UK or remote-UK, or ruled out by the "
    "profile's availability (e.g. a full-time experienced-hire role that starts now, when the candidate can "
    "only work part-time until a later date). When unsure, choose check or good, never skip: a wrongly "
    "skipped job is lost, a wrongly kept one costs a glance.\n"
    "sponsorship: 'offered' or 'not offered' only if the text says so, else 'not stated'; when "
    "sponsorship_in_text is given, that is what the advert says. It's reported, not a reason to skip unless "
    "the profile says sponsorship is needed.\n"
    "reason: one short phrase the candidate can act on."
)


class ListingVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="the listing id exactly as given")
    fit: Fit
    reason: str = Field(description="at most 12 words")
    sponsorship: Literal["offered", "not offered", "not stated"]
    start_date: str | None = Field(description="the start date as stated, e.g. 'September 2027', else null")
    experience_years: int | None = Field(description="years of experience asked for, else null")


class ScreeningReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdicts: list[ListingVerdict]


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
    fits: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        text = (
            f"{self.screened} screened ({self.sent_to_second_pass} to the second pass), "
            f"{self.already_screened} already screened, {self.deferred} left for the next run; "
            + ", ".join(f"{count} {fit}" for fit, count in sorted(self.fits.items()))
        )
        if self.failed_batches:
            text += f"; {self.failed_batches} batches failed"
        if self.stopped_by_usage_limit:
            text += "; stopped at the plan's usage limit"
        return text


def read_profile(profile_path: Path) -> str:
    profile = yaml.safe_load(profile_path.read_text()) or {}
    return "\n".join(f"{key}: {' '.join(str(value).split())}" for key, value in profile.items())


class LlmScreening:
    def __init__(
        self,
        screening_settings: LlmScreeningSettings,
        llm_client: LlmClient,
        repository: ScreeningRepository,
        profile: str,
    ):
        self._settings = screening_settings
        self._llm_client = llm_client
        self._repository = repository
        self._profile = profile
        self._profile_hash = hashlib.sha1(profile.encode()).hexdigest()

    def screen(self, listings: list[Listing]) -> ScreeningRunReport:
        report = ScreeningRunReport()
        stored = self._repository.find(listing.key for listing in listings)
        due = [listing for listing in listings if self._is_due(listing, stored.get(listing.key))]
        report.already_screened = len(listings) - len(due)
        report.to_screen = len(due)
        batch_listings = due[: self._settings.max_listings_per_run]
        report.deferred = len(due) - len(batch_listings)
        # verdicts gathered so far survive a usage limit hit midway
        verdicts: dict[str, tuple[ListingVerdict, str]] = {}
        try:
            self._run_pass(batch_listings, self._settings.first_pass_model, report, verdicts)
            # the stronger model only helps where there's advert text to read
            undecided = [
                listing
                for listing in batch_listings
                if listing.key in verdicts
                and verdicts[listing.key][0].fit == UNDECIDED
                and listing.primary.description
            ]
            report.sent_to_second_pass = len(undecided)
            self._run_pass(undecided, self._settings.second_pass_model, report, verdicts)
        except LlmUsageLimitError as error:
            logger.warning("LLM screening stopped: %s", error)
            report.stopped_by_usage_limit = True
        report.screened = len(verdicts)
        for verdict, _ in verdicts.values():
            report.fits[verdict.fit] = report.fits.get(verdict.fit, 0) + 1
        return report

    def _is_due(self, listing: Listing, stored: ListingScreening | None) -> bool:
        return stored is None or stored.content_hash != self._content_hash(listing)

    def _advert(self, listing: Listing) -> str | None:
        return advert_digest(
            listing.primary.description,
            self._settings.advert_opening_chars,
            self._settings.advert_key_fact_chars,
        )

    # what was sent: a different advert, profile or prompt means a new hash
    def _content_hash(self, listing: Listing) -> str:
        job = listing.primary
        content = "|".join([job.title, job.company_name, job.location or "", self._advert(listing) or ""])
        return hashlib.sha1(f"{self._profile_hash}|{PROMPT_VERSION}|{content}".encode()).hexdigest()

    # adds listing key -> (verdict, model) to verdicts; a failed batch is
    # skipped and its listings stay due for the next run
    def _run_pass(
        self,
        listings: list[Listing],
        model: str,
        report: ScreeningRunReport,
        verdicts: dict[str, tuple[ListingVerdict, str]],
    ) -> None:
        for batch in _chunks(listings, self._settings.batch_size):
            by_id = {str(position): listing for position, listing in enumerate(batch)}
            try:
                reply = self._llm_client.ask(self._prompt(by_id), ScreeningReply, SYSTEM_PROMPT, model)
            except LlmUsageLimitError:
                raise
            except LlmError as error:
                report.failed_batches += 1
                logger.warning("LLM screening batch failed on %s: %s", model, error)
                continue
            answered = {
                by_id[verdict.id].key: (verdict, model) for verdict in reply.verdicts if verdict.id in by_id
            }
            # stored straight away, so an interrupted run keeps what it paid for
            self._repository.save(self._screenings(batch, answered))
            verdicts.update(answered)

    def _prompt(self, by_id: dict[str, Listing]) -> str:
        lines = [
            json.dumps(
                {
                    "id": listing_id,
                    "title": listing.primary.title,
                    "company": listing.primary.company_name,
                    "location": listing.primary.location,
                    "type": listing.primary.employment_type,
                    "salary": listing.primary.salary_text,
                    "closes": listing.closes_at.date().isoformat() if listing.closes_at else None,
                    "advert": self._advert(listing),
                    # read from the whole advert by rule; null when it doesn't say
                    "sponsorship_in_text": sponsorship_in_advert(listing.primary.description),
                },
                ensure_ascii=False,
            )
            for listing_id, listing in by_id.items()
        ]
        return f"Candidate:\n{self._profile}\n\nListings (one JSON object per line):\n" + "\n".join(lines)

    def _screenings(
        self, listings: list[Listing], verdicts: dict[str, tuple[ListingVerdict, str]]
    ) -> list[ListingScreening]:
        return [
            ListingScreening(
                listing_key=listing.key,
                content_hash=self._content_hash(listing),
                fit=verdict.fit,
                reason=verdict.reason,
                # the rule reads the whole advert, so where it finds an answer it wins
                sponsorship=sponsorship_in_advert(listing.primary.description) or verdict.sponsorship,
                start_date=verdict.start_date,
                experience_years=verdict.experience_years,
                model=model,
                screened_at=utc_now(),
            )
            for listing in listings
            if listing.key in verdicts
            for verdict, model in [verdicts[listing.key]]
        ]


def _chunks(listings: list[Listing], size: int) -> Iterator[list[Listing]]:
    for start in range(0, len(listings), size):
        yield listings[start : start + size]
