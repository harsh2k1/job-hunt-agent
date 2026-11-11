import re
from collections.abc import Iterable


class TargetLocationMatcher:
    """Says whether a posting's locations include somewhere we're looking.

    Keywords match as whole words in any case, so "uk" matches "Remote - UK"
    and "London, UK" but not "Ukraine".
    """

    def __init__(self, keywords: Iterable[str]):
        escaped_keywords = sorted(
            (re.escape(keyword.strip()) for keyword in keywords if keyword.strip()), key=len
        )
        if not escaped_keywords:
            raise ValueError("at least one target location keyword is needed")
        # longest first, so "united kingdom" wins over a shorter overlapping keyword
        self._pattern = re.compile(rf"\b({'|'.join(reversed(escaped_keywords))})\b", re.IGNORECASE)

    def matches(self, location: str) -> bool:
        return self._pattern.search(location) is not None

    # the first target place named, lower case: "Greater London (Hybrid)" -> "london"
    def first_match(self, text: str) -> str | None:
        match = self._pattern.search(text)
        return match.group(1).lower() if match else None

    # "Data Scientist - London" -> "Data Scientist - "
    def remove_from(self, text: str) -> str:
        return self._pattern.sub(" ", text)

    # A posting with no location at all is kept: it can't be ruled out, and
    # anything unclear goes to a person rather than being dropped.
    def matches_any(self, locations: Iterable[str | None]) -> bool:
        known_locations = [location for location in locations if location]
        return not known_locations or any(self.matches(location) for location in known_locations)


def join_locations(locations: Iterable[str | None]) -> str | None:
    # de-duplicated, order kept; one string because most sources give one
    unique_locations = list(
        dict.fromkeys(location.strip() for location in locations if location and location.strip())
    )
    return "; ".join(unique_locations) or None
