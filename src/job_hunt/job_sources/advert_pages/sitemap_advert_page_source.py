from abc import abstractmethod
from typing import ClassVar

from job_hunt.job_sources.advert_page_reader import AdvertPageLink
from job_hunt.job_sources.advert_pages.advert_page_source import AdvertPageSource
from job_hunt.job_sources.sitemaps import read_sitemap


class SitemapAdvertPageSource(AdvertPageSource):
    """An advert-page site whose sitemap lists its adverts. Subclasses name
    the sitemap and say which of its URLs are adverts."""

    sitemap_url: ClassVar[str]
    # Whether the sitemap's lastmod really changes when an advert does. If
    # so, a changed advert is downloaded again straight away; if the site
    # stamps every entry with the same date, the cache expiry decides.
    sitemap_dates_are_per_advert: ClassVar[bool] = True

    def _list_advert_links(self) -> list[AdvertPageLink]:
        sitemap = read_sitemap(self._http_client.get_text(self.sitemap_url))
        return [
            AdvertPageLink(entry.url, entry.last_modified if self.sitemap_dates_are_per_advert else None)
            for entry in sitemap
            if self._is_advert_url(entry.url)
        ]

    @abstractmethod
    def _is_advert_url(self, url: str) -> bool: ...
