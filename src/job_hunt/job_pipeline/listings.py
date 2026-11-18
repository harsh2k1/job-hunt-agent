"""One listing per advert, however many sources found it.

The same job turns up on the company's board, LinkedIn, Google Jobs and an
alert email. Jobs stay stored per source (each source's own id is how a
sighting is recognised tomorrow); listings group them when read, and show
each from its most direct source.
"""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from job_hunt.job_sources.job_source import UNKNOWN_COMPANY_NAME
from job_hunt.job_sources.job_source_factory import JobSourceFactory
from job_hunt.job_sources.location_matching import TargetLocationMatcher
from job_hunt.job_store.company_names import normalise_company_name
from job_hunt.job_store.models import StoredJob

# "(m/f/d)", "[Remote]", "(2027 start)": noise when comparing titles
BRACKETED = re.compile(r"[(\[{][^)\]}]*[)\]}]")
NON_WORD = re.compile(r"[^a-z0-9]+")
# a place that names the country, not where the job is: one source says
# "United Kingdom" where another says "Cambridge" for the same advert
BROAD_PLACES = frozenset({"", "uk", "united kingdom", "england", "great britain", "britain"})


@dataclass(frozen=True)
class Listing:
    key: str
    # every sighting, the most direct source first
    sightings: tuple[StoredJob, ...]
    # keys this listing had before sightings of it were merged in (a broad
    # place joined its city), so what was kept under them still finds it
    merged_keys: tuple[str, ...] = ()

    @property
    def primary(self) -> StoredJob:
        return self.sightings[0]

    @property
    def sources(self) -> list[str]:
        return list(dict.fromkeys(job.source for job in self.sightings))

    # sources don't always agree; the earliest known deadline is the safe one
    @property
    def closes_at(self) -> datetime | None:
        return min((job.closes_at for job in self.sightings if job.closes_at), default=None)


class ListingBuilder:
    def __init__(self, place_matcher: TargetLocationMatcher, source_rank: Callable[[str], int] | None = None):
        self._place_matcher = place_matcher
        self._source_rank = source_rank or JobSourceFactory.listing_rank

    # None: too little to match on, so the job stands alone
    def listing_key(self, job: StoredJob) -> str | None:
        company = normalise_company_name(job.company_name)
        if not company or job.company_name == UNKNOWN_COMPANY_NAME:
            return None
        title_words = NON_WORD.sub(
            " ", self._place_matcher.remove_from(BRACKETED.sub(" ", job.title.lower()))
        )
        title = " ".join(title_words.replace(" and ", " ").split())
        place = self._place_matcher.first_match(job.location or "") or ""
        return f"{company}|{title}|{place}" if title else None

    def build(self, jobs: Iterable[StoredJob]) -> list[Listing]:
        groups: dict[str, list[StoredJob]] = {}
        for job in jobs:
            key = self.listing_key(job) or f"{job.source}:{job.source_job_id}"
            groups.setdefault(key, []).append(job)
        merged = _merge_broad_places(groups)
        return [
            Listing(key, tuple(sorted(group, key=self._preference)), tuple(merged.get(key, ())))
            for key, group in groups.items()
        ]

    # most direct source first; then the sighting with an advert text; then
    # the most recently seen
    def _preference(self, job: StoredJob) -> tuple[int, bool, float]:
        return (self._source_rank(job.source), job.description is None, -job.last_seen_at.timestamp())


# The same role at the same employer, once with a city and once with only
# the country (or no place), is one advert: the broad sightings join the
# city's listing; sightings that all name only the country ("England",
# "UK") are one listing too. With several cities (a scheme in Leeds and
# Edinburgh) it's unclear which the broad one is, so nothing is merged.
# Returns city key -> the broad keys merged into it.
def _merge_broad_places(groups: dict[str, list[StoredJob]]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    keys_by_role: dict[tuple[str, str], list[str]] = {}
    for key in groups:
        parts = key.split("|")
        if len(parts) == 3:
            keys_by_role.setdefault((parts[0], parts[1]), []).append(key)
    for keys in keys_by_role.values():
        cities = [key for key in keys if key.rsplit("|", 1)[1] not in BROAD_PLACES]
        broad = [key for key in keys if key.rsplit("|", 1)[1] in BROAD_PLACES]
        if len(cities) > 1 or not broad:
            continue
        # into the city, or into the first broad one when there's no city
        target, rest = (cities[0], broad) if cities else (broad[0], broad[1:])
        if rest:
            for key in rest:
                groups[target].extend(groups.pop(key))
            merged[target] = rest
    return merged
