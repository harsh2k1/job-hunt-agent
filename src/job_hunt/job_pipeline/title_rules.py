"""The free first sort of job titles, before any LLM sees them.

A title with a keep term (data, analyst, ML, Python...) goes on to the
advert reading. A title with a drop term (pharmacist, sales, civil engineer,
software engineer...) and no keep term is unrelated: keep wins, so
"Marketing Data Analyst" stays. Drop terms name other trades only, so a
relevant title isn't lost; whatever matches neither list goes to the LLM.
"""

import re
from collections.abc import Iterable
from typing import Literal

from job_hunt.common.settings_schema import TitleRulesSettings

TitleRuling = Literal["keep", "drop"]


def whole_words(terms: Iterable[str]) -> re.Pattern[str]:
    escaped = sorted((re.escape(term.strip()) for term in terms if term.strip()), key=len, reverse=True)
    return re.compile(rf"\b(?:{'|'.join(escaped)})\b", re.IGNORECASE)


class TitleRules:
    def __init__(self, title_rules_settings: TitleRulesSettings):
        self._keep = whole_words(title_rules_settings.keep_terms)
        self._drop = whole_words(title_rules_settings.drop_terms)

    # None: the rules can't tell, so the LLM judges it
    def rule(self, title: str) -> TitleRuling | None:
        if self._keep.search(title):
            return "keep"
        if self._drop.search(title):
            return "drop"
        return None
