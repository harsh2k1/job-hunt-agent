"""The verdict a listing ends up with, worked out afresh every time the
sheet is built from the facts the LLM read out of its advert (stored) and
the rules, so a refined fact or a changed rule takes effect at once without
reading any advert again. The two things decided at read time are kept:
the role is unrelated to the candidate's fields, or its field couldn't be
told."""

from dataclasses import dataclass

from job_hunt.job_pipeline.eligibility import eligibility, sponsorship_rule
from job_hunt.job_pipeline.llm_screening import UNCLEAR_FIELD_REASON, UNRELATED_REASON
from job_hunt.job_store.models import ListingScreening

NOT_SCREENED = "not screened yet"
# related to nothing the candidate does: not shown on any tab
UNRELATED = "unrelated"


@dataclass(frozen=True)
class FinalVerdict:
    # apply / check / skip (related, but can't be taken) / unrelated / not screened yet
    verdict: str
    # why it's skipped or to be checked
    reason: str = ""
    # bullets on why it's valid, for "apply"
    valid_because: str | None = None


def final_verdict(screening: ListingScreening | None, licence: str, visa_ends: str) -> FinalVerdict:
    if screening is None:
        return FinalVerdict(NOT_SCREENED)
    if screening.reason.startswith(UNRELATED_REASON):
        return FinalVerdict(UNRELATED, screening.reason)
    kind = screening.kind or "full-time"
    part_time = bool(screening.part_time)
    timing = eligibility(kind, part_time, screening.starts_when_free or "not stated")
    if timing.eligible is False:
        return FinalVerdict("skip", f"Not applicable: {timing.reason}")
    visa = sponsorship_rule(kind, part_time, screening.sponsorship, licence, visa_ends, screening.role_end)
    if visa.eligible is False:
        return FinalVerdict("skip", f"Not applicable: {visa.reason}")
    if screening.reason == UNCLEAR_FIELD_REASON:
        return FinalVerdict("check", screening.reason)
    for rule in (timing, visa):
        if rule.eligible is None:
            return FinalVerdict("check", rule.reason[0].upper() + rule.reason[1:])
    return FinalVerdict("apply", valid_because=_bullets(screening, timing.basis, visa.basis, licence))


# why it's valid: the timing rule, the field, the start, and how the visa is covered
def _bullets(screening: ListingScreening, timing_basis: str, visa_basis: str, licence: str) -> str:
    bullets = [timing_basis]
    if screening.role_field:
        bullets.append(f"{screening.role_field[0].upper()}{screening.role_field[1:]} role")
    if screening.start_date:
        bullets.append(f"Starts {screening.start_date}")
    if not (screening.part_time or screening.kind in ("part-time", "internship")):
        if visa_basis:
            bullets.append(visa_basis)
        elif screening.sponsorship == "offered":
            bullets.append("Advert offers visa sponsorship")
        else:
            bullets.append(f"Employer holds a sponsor licence ({licence} match on the register)")
    return "\n".join(f"• {bullet}" for bullet in bullets if bullet)
