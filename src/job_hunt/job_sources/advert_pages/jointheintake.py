import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from job_hunt.common.settings_schema import JoinTheIntakeSourceSettings
from job_hunt.job_sources.advert_pages.advert_page_source import AdvertPageLink, AdvertPageSource
from job_hunt.job_sources.job_posting_markup import JobPostingMarkup
from job_hunt.job_sources.job_source import register_job_source
from job_hunt.job_sources.sitemaps import read_sitemap

SITEMAP_URL = "https://www.jointheintake.com/sitemap.xml"
# the markup has no validThrough; the deadline is written into the description
CLOSING_DATE_PATTERN = re.compile(r"Closing date:\s*(?:</strong>)?\s*(\d{1,2} [A-Z][a-z]+ \d{4})")


@register_job_source
class JoinTheIntakeSource(AdvertPageSource):
    """The Intake: UK graduate schemes, internships, placements and spring
    weeks with their opening and closing dates. Its sitemap lists every
    advert with the date it last changed, so after the first run only
    changed adverts are downloaded again.
    """

    name = "jointheintake"
    _source_settings: JoinTheIntakeSourceSettings

    def _list_advert_links(self) -> list[AdvertPageLink]:
        sitemap = read_sitemap(self._http_client.get_text(SITEMAP_URL))
        return [
            AdvertPageLink(entry.url, entry.last_modified)
            for entry in sitemap
            if self._is_advert_in_followed_section(entry.url)
        ]

    # adverts sit one level under their section, e.g.
    # /graduate-schemes/abound-junior-software-engineer-london-1cc7ca;
    # /graduate-schemes itself and /graduate-schemes/accounting are listings
    def _is_advert_in_followed_section(self, url: str) -> bool:
        path_parts = urlsplit(url).path.strip("/").split("/")
        return (
            len(path_parts) == 2
            and path_parts[0] in self._source_settings.sections
            and bool(re.search(r"-[a-z0-9]{6}$", path_parts[1]))
        )

    def _closing_date(self, job_posting: JobPostingMarkup) -> datetime | None:
        match = CLOSING_DATE_PATTERN.search(job_posting.get("description") or "")
        if match is None:
            # "Rolling, so it closes once the cohort is full"
            return None
        try:
            return datetime.strptime(match.group(1), "%d %B %Y").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
