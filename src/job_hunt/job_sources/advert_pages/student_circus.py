from urllib.parse import urlsplit

from job_hunt.job_sources.advert_pages.sitemap_advert_page_source import SitemapAdvertPageSource
from job_hunt.job_sources.job_source import register_job_source


@register_job_source
class StudentCircusSource(SitemapAdvertPageSource):
    """Student Circus: jobs at employers that sponsor visas, for international
    students. Every advert page is public with JobPosting markup (title,
    employer, place, posted and closing dates); only the description needs
    a login, so none is used. Its sitemap lists every advert.
    """

    name = "student_circus"
    display_name = "Student Circus"
    sitemap_url = "https://studentcircus.com/sitemap/job-sitemap.xml"
    # every entry carries the same date, so it says nothing about changes
    sitemap_dates_are_per_advert = False

    # adverts are /jobs/<slug>; a few category pages (/jobs/others) share the
    # pattern and are dropped later for having no JobPosting markup
    def _is_advert_url(self, url: str) -> bool:
        path_parts = urlsplit(url).path.strip("/").split("/")
        return len(path_parts) == 2 and path_parts[0] == "jobs"
