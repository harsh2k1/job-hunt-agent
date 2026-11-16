"""Facts read straight from an advert's full text, without an LLM.

Adverts bury the decisive lines (visa sponsorship, start date, years of
experience) anywhere, often thousands of characters in. These rules read
the whole text, so those lines can't be lost to a length limit, and they
cost nothing. Wording was taken from real adverts (Nov 2026).
"""

import re
from typing import Literal

Sponsorship = Literal["offered", "not offered"]

SENTENCE_BREAK = re.compile(r"(?<=[.!?;])\s+|\n+|\s+[•\-–]\s+")
# a sentence about visas / the right to work (not "sponsored events" or
# "executive sponsor", which are far more common in adverts)
VISA_CONTEXT = re.compile(
    r"\bvisas?\b|right to work|work permit|immigration|skilled worker|sponsor register|"
    r"sponsorship (?:now or )?in the future|(?:visa|work|employment) sponsorship|"
    r"sponsor (?:a |your )?(?:work )?visas?|"
    r"(?:require|requiring|need|needing|offer|offering|provide|providing|without) (?:visa )?sponsorship|"
    r"sponsorship (?:is |will be )?(?:not )?(?:available|offered|provided)|no sponsorship",
    re.IGNORECASE,
)
NOT_OFFERED = re.compile(
    # adverts use both ' and ’ in can't, don't, won't, isn't
    r"\b(?:cannot|can ?not|can['’]t|unable to|not able to|are not able to|is not able to|do not|don['’]t|"
    r"does not|doesn['’]t|will not|won['’]t|not in a position to)\b[^.]{0,60}\bsponsor|"
    r"\b(?:isn['’]t|is not|not) open to (?:visa )?sponsorship|"
    r"\bnot eligible for\b[^.]{0,40}\bsponsorship|"
    r"\bunsponsored\b|"
    r"\bno (?:visa |work )?sponsorship\b|"
    r"\bsponsorship (?:is |will )?(?:not|unavailable)\b|"
    r"\bnot (?:be )?(?:able to )?(?:offer|provide)(?:ing)? (?:visa |work )?sponsorship|"
    r"\bwithout (?:the need for |needing |requiring |requirement for )?(?:visa |any )?sponsorship\b|"
    r"\bnot (?:on|registered on) (?:the )?[^.]{0,40}sponsor register\b|"
    r"\b(?:not|no longer) (?:a )?(?:licensed |registered )?(?:visa )?sponsor\b",
    re.IGNORECASE,
)
OFFERED = re.compile(
    r"\b(?:we|will|can|able to|happy to|do|does)\b[^.]{0,40}\b(?:provide|offer|support)s?\b[^.]{0,30}"
    r"\b(?:visa )?sponsorship\b|"
    r"\b(?:visa |work )?sponsorship (?:is |may be |can be )?(?:available|offered|provided)\b|"
    r"\b(?:we|will|can|able to|happy to)\b[^.]{0,20}\bsponsor\b[^.]{0,30}\bvisas?\b|"
    r"\bvisa sponsorship\s*:\s*(?:yes|available|we provide)\b|"
    r"\bsupport\b[^.]{0,60}\bthrough the appropriate (?:uk )?visa routes?\b|"
    r"\beffort to get you a visa\b|"
    r"\bsponsorship (?:may be|is) possible\b|"
    r"\beligible\b[^.]{0,30}\bfor (?:visa )?sponsorship\b|"
    r"\bvisa sponsorship (?:where|if|when) (?:applicable|eligible|required|needed)\b|"
    r"\bvisa sponsorship\b[^.]{0,50}\bavailable\b|"
    r"\bconsiders? (?:visa |work )?sponsorship\b|"
    r"\bqualify for\b[^.]{0,25}\bsponsorship\b|"
    r"\b(?:will |can )?provide (?:a |you with a )?(?:skilled worker )?(?:work )?visa\b|"
    r"\baccept\w*\b[^.]{0,50}\brequire (?:visa )?sponsorship\b",
    re.IGNORECASE,
)


def visa_sentences(text: str) -> list[str]:
    return [sentence for sentence in SENTENCE_BREAK.split(text) if VISA_CONTEXT.search(sentence)]


# None when the advert doesn't say either way. "Not offered" wins when an
# advert says both (e.g. sponsorship for some roles but not this one).
def sponsorship_in_advert(text: str | None) -> Sponsorship | None:
    if not text:
        return None
    sentences = visa_sentences(text)
    if any(NOT_OFFERED.search(sentence) for sentence in sentences):
        return "not offered"
    if any(OFFERED.search(sentence) for sentence in sentences):
        return "offered"
    return None


# sentences that decide whether the candidate can apply, wherever they are
KEY_FACT = re.compile(
    r"\bvisas?\b|sponsor|right to work|work permit|eligib|\bstart(?:s|ing)? (?:date|in|on|from)\b|commenc|"
    r"\bintake\b|graduat|years?(?:'|’)? (?:of )?(?:professional |relevant |industry )?experience|"
    r"part[- ]time|hours (?:a|per) week|\bfte\b|closing date|deadline|penultimate|final[- ]year|"
    r"\b(?:\d+|one|two|three|four)[- ](?:year|month)s?\b|fixed[- ]term|\bcontract\b|\bduration\b|"
    r"\brotations?\b|"
    r"\bpermanent\b",
    re.IGNORECASE,
)


# What the LLM reads of an advert: its opening, plus every sentence from
# anywhere in it that bears on eligibility, so the deciding lines are never
# cut off by a length limit.
def advert_digest(text: str | None, opening_chars: int, key_fact_chars: int) -> str | None:
    if not text:
        return None
    opening = text[:opening_chars]
    key_facts: list[str] = []
    used = 0
    for sentence in SENTENCE_BREAK.split(text[opening_chars:]):
        sentence = " ".join(sentence.split())
        if sentence and KEY_FACT.search(sentence) and sentence not in key_facts:
            if used + len(sentence) > key_fact_chars:
                break
            key_facts.append(sentence)
            used += len(sentence)
    return opening + ("\n[key lines from later in the advert]\n" + "\n".join(key_facts) if key_facts else "")
