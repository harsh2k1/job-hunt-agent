"""Whether a site's robots.txt lets us read a page, for sources that read
pages on sites nobody vetted by hand (an employer's careers site found by a
web search). Each site's robots.txt is fetched once and cached.

The usual reading of a missing file: 4xx (no robots.txt) allows everything;
a server error or no answer at all is taken as "not now" for that run."""

from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from job_hunt.common.cache import Cache
from job_hunt.common.cache.backends import CACHE_MISS
from job_hunt.common.http_client import HttpClient, HttpRequestError


class RobotsPolicy:
    def __init__(self, http_client: HttpClient, cache: Cache, user_agent: str, cache_ttl_hours: float):
        self._http_client = http_client
        self._cache = cache
        self._user_agent = user_agent
        self._cache_ttl_hours = cache_ttl_hours

    def allows(self, url: str) -> bool:
        parts = urlsplit(url)
        if not parts.scheme or not parts.hostname:
            return False
        rules = self._rules(f"{parts.scheme}://{parts.netloc}")
        if rules is None:
            return False
        parser = RobotFileParser()
        parser.parse(rules.splitlines())
        return parser.can_fetch(self._user_agent, url)

    # the robots.txt text ("" when the site has none); None when it couldn't be read
    def _rules(self, site: str) -> str | None:
        cache_key = f"robots:{site}"
        cached = self._cache.get(cache_key, default=CACHE_MISS)
        if cached is not CACHE_MISS:
            return cached
        try:
            rules = self._http_client.get_text(f"{site}/robots.txt")
        except HttpRequestError as error:
            if error.status_code is None or error.status_code >= 500:
                return None
            rules = ""
        self._cache.set(cache_key, rules, ttl_hours=self._cache_ttl_hours)
        return rules
