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


Sponsorship = Literal["offered", "not offered", "not stated"]
# what the Home Office register says about the employer's name
SponsorLicence = Literal["exact", "possible", "not found", ""]
# does the role end by the time the candidate's Graduate visa does?
# "by": yes (a 12-month programme from September 2027); "after": it runs past
# it, is permanent or leads into a permanent role; None: not asked
RoleEnd = Literal["by", "after", "not stated"]


# A role that runs past the candidate's Graduate visa needs an employer that
# can sponsor them; one that ends before it doesn't, whatever the advert
# says about sponsorship. Part-time roles and internships end before it.
# An employer without a sponsor licence can't sponsor, but the register is
# by legal name and misses some brands, so that's a check, not a skip. A
# length the advert doesn't give is taken as running past (most graduate
# schemes and jobs lead into a permanent role), and the reason says so.
def sponsorship_rule(
    kind: RoleKind,
    part_time: bool,
    sponsorship: Sponsorship,
    licence: SponsorLicence,
    visa_ends: str,
    role_end: RoleEnd | None = None,
) -> Eligibility:
    if part_time or kind in ("part-time", "internship"):
        return Eligibility(True)
    if role_end == "by":
        return Eligibility(
            True, basis=f"Ends by {visa_ends}, within your Graduate visa: no sponsorship needed"
        )
    length = (
        "the advert doesn't give its length, so taken as running" if role_end == "not stated" else "it runs"
    )
    if sponsorship == "not offered":
        return Eligibility(
            False, f"no visa sponsorship, and {length} past your Graduate visa (ends {visa_ends})"
        )
    if sponsorship == "not stated" and licence == "not found":
        return Eligibility(None, "no sponsor licence found under this name; confirm they can sponsor")
    return Eligibility(True)
