"""Whether the candidate can apply at all, decided in code from facts the
LLM reads out of the advert (kind of role, part-time, start), so the rule
is exact and testable instead of left to the model's judgement.

The candidate can take: graduate roles; part-time roles; internships that
are part-time or start once they're free; any other full-time role only
if it starts once they're free.
"""

from dataclasses import dataclass
from typing import Literal

RoleKind = Literal["graduate", "internship", "part-time", "full-time", "other"]
# does the advert's start fall on or after the date the candidate is free?
StartsWhenFree = Literal["yes", "no", "not stated"]


@dataclass(frozen=True)
class Eligibility:
    # None: can't tell from the advert (shown as "check")
    eligible: bool | None
    # why not, or what to confirm
    reason: str = ""
    # why the candidate can apply, when they can
    basis: str = ""


def eligibility(kind: RoleKind, part_time: bool, starts_when_free: StartsWhenFree) -> Eligibility:
    if part_time or kind == "part-time":
        return Eligibility(True, basis="Part-time role, open to you now")
    if kind == "graduate":
        if starts_when_free == "no":
            return Eligibility(False, "graduate role starting before you're free full-time")
        return Eligibility(True, basis="Graduate role")
    if kind == "internship":
        if starts_when_free == "yes":
            return Eligibility(True, basis="Internship starting once you're free full-time")
        if starts_when_free == "no":
            return Eligibility(False, "full-time internship before you're free full-time")
        return Eligibility(None, "internship: confirm it's part-time or starts once you're free")
    # full-time and anything else: only once the candidate is free full-time
    if starts_when_free == "yes":
        return Eligibility(True, basis=f"{kind.capitalize()} role starting once you're free full-time")
    return Eligibility(False, f"{kind} role, not starting once you're free full-time")
