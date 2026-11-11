from job_hunt.job_sources.advert_page_reader import AdvertPage
from job_hunt.job_sources.advert_pages.sitemap_advert_page_source import SitemapAdvertPageSource
from job_hunt.job_sources.job_source import register_job_source


@register_job_source
class MetaCareersSource(SitemapAdvertPageSource):
    """Meta's careers site: its jobs sitemap (listed in robots.txt) names
    every job page, and each carries JobPosting markup. Worldwide, so the
    location check keeps the UK ones."""

    name = "meta_careers"
    listing_rank = 20
    sitemap_url = "https://www.metacareers.com/jobsearch/sitemap.xml"

    def _is_advert_url(self, url: str) -> bool:
        return "/job_details/" in url

    # /profile/job_details/1661314105605589/ -> 1661314105605589
    def _source_job_id(self, advert_page: AdvertPage) -> str:
        return advert_page.url.rstrip("/").rsplit("/", 1)[-1]
