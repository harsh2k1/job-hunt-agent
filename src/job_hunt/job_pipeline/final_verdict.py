"""The verdict a listing ends up with, from what the LLM read out of its
advert (stored) plus rules applied when the sheet is built, so a rule
change never needs the adverts read again."""

from dataclasses import dataclass

from job_hunt.job_pipeline.eligibility import sponsorship_rule
from job_hunt.job_store.models import ListingScreening

NOT_SCREENED = "not screened yet"


@dataclass(frozen=True)
class FinalVerdict:
    # apply / check / skip / not screened yet
    verdict: str
    # why it's skipped or to be checked
    reason: str = ""
    # bullets on why it's valid, for "apply"
    valid_because: str | None = None


def final_verdict(screening: ListingScreening | None, licence: str, visa_ends: str) -> FinalVerdict:
    if screening is None:
        return FinalVerdict(NOT_SCREENED)
    if screening.fit == "skip":
        return FinalVerdict("skip", screening.reason)
    rule = sponsorship_rule(
        screening.kind or "full-time",
        bool(screening.part_time),
        screening.sponsorship,
        licence,
        visa_ends,
        screening.role_end,
    )
    if rule.eligible is False:
        return FinalVerdict("skip", f"Not applicable: {rule.reason}")
    if screening.fit == "check":
        return FinalVerdict("check", screening.reason)
    if rule.eligible is None:
        return FinalVerdict("check", rule.reason[0].upper() + rule.reason[1:])
    return FinalVerdict("apply", valid_because=_with_sponsorship(screening, licence, rule.basis))


# for roles that outlast the Graduate visa, say how sponsorship is covered
def _with_sponsorship(screening: ListingScreening, licence: str, visa_basis: str) -> str | None:
    bullets = screening.valid_because
    if screening.part_time or screening.kind in ("part-time", "internship"):
        return bullets
    if visa_basis:
        return f"{bullets}\n• {visa_basis}" if bullets else f"• {visa_basis}"
    if screening.sponsorship == "offered":
        note = "Advert offers visa sponsorship"
    else:
        note = f"Employer holds a sponsor licence ({licence} match on the register)"
    return f"{bullets}\n• {note}" if bullets else f"• {note}"
