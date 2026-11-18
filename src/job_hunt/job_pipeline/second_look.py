"""A second, narrow question about a few listings the advert read left open
(kind_refinement.py, field_recheck.py): the listings go in batches, one line
each, and each answer is matched back to its listing by id."""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from job_hunt.common.logger import get_logger
from job_hunt.common.settings_schema import LlmScreeningSettings
from job_hunt.job_pipeline.advert_facts import advert_digest
from job_hunt.job_pipeline.listings import Listing
from job_hunt.llm.llm_client import LlmClient, LlmError, LlmUsageLimitError

logger = get_logger(__name__)


class ListingAnswer(Protocol):
    id: str


AnswerT = TypeVar("AnswerT", bound=ListingAnswer)


@dataclass
class SecondLookReport:
    asked: int = 0
    failed_batches: int = 0
    stopped_by_usage_limit: bool = False


def listing_line(listing: Listing, settings: LlmScreeningSettings) -> str:
    advert = advert_digest(
        listing.primary.description, settings.advert_opening_chars, settings.advert_key_fact_chars
    )
    return f"{listing.primary.title} at {listing.primary.company_name}: " + (
        advert or "(no advert text)"
    ).replace("\n", " ")


# Yields (listing, answer) for every answered listing; a failed batch is
# counted and skipped (its listings are asked again next run), and a usage
# limit stops the rest.
def ask_about_listings(
    listings: list[Listing],
    answers_of: Callable[[Any], list[AnswerT]],
    reply_schema: type[BaseModel],
    system_prompt: str,
    llm_client: LlmClient,
    settings: LlmScreeningSettings,
    report: SecondLookReport,
    name: str,
) -> Iterator[tuple[Listing, AnswerT]]:
    for start in range(0, len(listings), settings.batch_size):
        by_id = {
            str(position): listing
            for position, listing in enumerate(listings[start : start + settings.batch_size])
        }
        lines = "\n".join(
            f"[{listing_id}] {listing_line(listing, settings)}" for listing_id, listing in by_id.items()
        )
        try:
            reply = llm_client.ask(
                f"Listings:\n{lines}", reply_schema, system_prompt, settings.second_pass_model
            )
        except LlmUsageLimitError as error:
            logger.warning("%s stopped: %s", name, error)
            report.stopped_by_usage_limit = True
            return
        except LlmError as error:
            report.failed_batches += 1
            logger.warning("%s batch failed: %s", name, error)
            continue
        for answer in answers_of(reply):
            if answer.id in by_id:
                report.asked += 1
                yield by_id[answer.id], answer
