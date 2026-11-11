"""The cheap first pass over listings: plain rules, no LLM. Whatever it
skips is kept with its reason (the Skipped tab), never deleted, and the
LLM screening only ever sees what it keeps."""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from job_hunt.common.settings_schema import ScreeningSettings
from job_hunt.job_pipeline.listings import Listing


@dataclass(frozen=True)
class ScreeningResult:
    kept: bool
    # why it was skipped; None when kept
    reason: str | None = None


def whole_word_pattern(terms: Iterable[str]) -> re.Pattern[str]:
    escaped = sorted((re.escape(term.strip()) for term in terms if term.strip()), key=len, reverse=True)
    return re.compile(rf"\b({'|'.join(escaped)})\b", re.IGNORECASE)


class RuleScreening:
    def __init__(self, screening_settings: ScreeningSettings):
        self._relevant_title = whole_word_pattern(screening_settings.relevant_title_terms)
        self._senior_title = whole_word_pattern(screening_settings.senior_title_terms)

    # rules in order; the first that applies is the reason given
    def screen(self, listing: Listing, now: datetime) -> ScreeningResult:
        title = listing.primary.title
        if listing.closes_at is not None and listing.closes_at < now:
            return ScreeningResult(False, "closing date has passed")
        if not self._relevant_title.search(title):
            return ScreeningResult(False, "title isn't a data / AI role")
        senior = self._senior_title.search(title)
        if senior:
            return ScreeningResult(False, f"senior role ({senior.group(1).lower()})")
        return ScreeningResult(True)
