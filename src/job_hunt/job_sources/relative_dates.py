import re
from datetime import datetime, timedelta

# "3 days ago", "an hour ago", "30+ days ago" — how Google shows posting age
RELATIVE_AGE_PATTERN = re.compile(r"(\d+|an?)\+?\s+(minute|hour|day|week|month|year)s?\s+ago", re.IGNORECASE)

# months and years are approximate; only used to sort and to spot stale adverts
UNIT_LENGTHS = {
    "minute": timedelta(minutes=1),
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(weeks=1),
    "month": timedelta(days=30),
    "year": timedelta(days=365),
}


def parse_relative_age(text: str | None, now: datetime) -> datetime | None:
    if not text:
        return None
    match = RELATIVE_AGE_PATTERN.search(text)
    if match is None:
        return None
    amount_text, unit = match.groups()
    amount = 1 if amount_text.lower() in ("a", "an") else int(amount_text)
    return now - amount * UNIT_LENGTHS[unit.lower()]
