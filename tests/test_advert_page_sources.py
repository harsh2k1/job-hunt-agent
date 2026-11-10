"""JoinTheIntake and jobs.ac.uk against pages shaped like the live sites
(trimmed copies of what they served in Nov 2026)."""

import json

import httpx

from job_hunt.common.http_client import HttpClient
from job_hunt.common.settings import settings
from job_hunt.job_sources.advert_pages.jobs_ac_uk import JobsAcUkSource
from job_hunt.job_sources.advert_pages.jointheintake import JoinTheIntakeSource
from job_hunt.job_sources.sitemaps import read_sitemap

INTAKE_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.jointheintake.com/graduate-schemes</loc></url>
  <url><loc>https://www.jointheintake.com/graduate-schemes/accounting</loc></url>
  <url><loc>https://www.jointheintake.com/employers/abound-1cc7ca</loc></url>
  <url>
    <loc>https://www.jointheintake.com/graduate-schemes/abound-junior-software-engineer-london-1cc7ca</loc>
    <lastmod>2026-11-09</lastmod>
  </url>
  <url>
    <loc>https://www.jointheintake.com/training-contracts/slaughter-and-may-training-contract-london-a1b2c3</loc>
    <lastmod>2026-11-04</lastmod>
  </url>
  <url>
    <loc>https://www.jointheintake.com/internships/tesco-pricing-analyst-reigate-k019bf</loc>
    <lastmod>2026-11-06</lastmod>
  </url>
  <url>
    <loc>https://www.jointheintake.com/internships/google-financial-analyst-intern-2027-dublin-8lfkbo</loc>
    <lastmod>2026-11-06</lastmod>
  </url>
</urlset>"""


def intake_advert(title: str, employer: str, locality: str, country: str, closing: str) -> str:
    markup = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": title,
        "description": (
            f"<p>{employer} is hiring.</p><ul><li><strong>Location:</strong> {locality}</li>"
            f"<li><strong>Closing date:</strong> {closing}</li></ul>"
        ),
        "datePosted": "2026-10-30",
        "employmentType": ["FULL_TIME"],
        "hiringOrganization": {"@type": "Organization", "name": employer},
        "jobLocation": {
            "@type": "Place",
            "address": {"addressLocality": locality, "addressCountry": country},
        },
    }
    return (
        f'<html><script type="application/ld+json">{json.dumps(markup)}</script>'
        '<a href="https://jobs.ashbyhq.com/abound/582538c4?utm_source=intake">Apply</a></html>'
    )


INTAKE_PAGES = {
    "/graduate-schemes/abound-junior-software-engineer-london-1cc7ca": intake_advert(
        "Junior Software Engineer", "Abound", "London", "GB", "Rolling, so it closes once the cohort is full"
    ),
    "/internships/tesco-pricing-analyst-reigate-k019bf": intake_advert(
        "Pricing Analyst Intern", "Tesco", "Reigate", "GB", "30 November 2026"
    ),
    "/internships/google-financial-analyst-intern-2027-dublin-8lfkbo": intake_advert(
        "Financial Analyst Intern", "Google", "Dublin", "IE", "1 December 2026"
    ),
}


class FakeSite:
    def __init__(self, pages: dict[str, str]):
        self.paths: list[str] = []
        self._pages = pages

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        body = self._pages.get(request.url.path)
        if callable(body):
            body = body(request)
        return httpx.Response(404) if body is None else httpx.Response(200, text=body)


def make_intake(fake_site: FakeSite, cache) -> JoinTheIntakeSource:
    return JoinTheIntakeSource(
        settings.job_search,
        settings.job_sources.jointheintake,
        HttpClient(settings.http, httpx.MockTransport(fake_site)),
        cache,
    )


def intake_site() -> FakeSite:
    return FakeSite({"/sitemap.xml": INTAKE_SITEMAP, **INTAKE_PAGES})


def test_sitemap_entries_are_read_in_any_namespace():
    entries = read_sitemap(INTAKE_SITEMAP)
    assert len(entries) == 7
    assert entries[3].last_modified == "2026-11-09"
    assert entries[0].last_modified is None


def test_only_adverts_in_followed_sections_are_downloaded(cache):
    fake_site = intake_site()
    make_intake(fake_site, cache).fetch_jobs()
    # listings, employer pages and the training contract (law-only section) are skipped
    assert sorted(fake_site.paths) == sorted(["/sitemap.xml", *INTAKE_PAGES])


def test_uk_adverts_are_kept_with_their_details(cache):
    jobs = {job.company_name: job for job in make_intake(intake_site(), cache).fetch_jobs()}

    # Dublin is outside every target location
    assert sorted(jobs) == ["Abound", "Tesco"]
    abound = jobs["Abound"]
    assert abound.location == "London, United Kingdom"
    assert abound.employer_url == "https://jobs.ashbyhq.com/abound/582538c4"
    assert abound.source_job_id == "/graduate-schemes/abound-junior-software-engineer-london-1cc7ca"
    assert abound.posted_at.isoformat() == "2026-10-30T00:00:00+00:00"
    # rolling deadline: no date to keep
    assert abound.closes_at is None
    assert jobs["Tesco"].closes_at.isoformat() == "2026-11-30T00:00:00+00:00"


def test_unchanged_adverts_come_from_the_cache_on_the_next_run(cache):
    fake_site = intake_site()
    make_intake(fake_site, cache).fetch_jobs()
    make_intake(fake_site, cache).fetch_jobs()
    assert fake_site.paths.count("/internships/tesco-pricing-analyst-reigate-k019bf") == 1


def test_a_changed_advert_is_downloaded_again(cache):
    make_intake(intake_site(), cache).fetch_jobs()
    changed_sitemap = INTAKE_SITEMAP.replace("2026-11-09", "2026-11-10")
    fake_site = FakeSite({"/sitemap.xml": changed_sitemap, **INTAKE_PAGES})
    make_intake(fake_site, cache).fetch_jobs()
    assert fake_site.paths == [
        "/sitemap.xml",
        "/graduate-schemes/abound-junior-software-engineer-london-1cc7ca",
    ]


def test_one_broken_advert_doesnt_stop_the_rest(cache):
    pages = {"/sitemap.xml": INTAKE_SITEMAP, **INTAKE_PAGES}
    del pages["/internships/tesco-pricing-analyst-reigate-k019bf"]
    jobs = make_intake(FakeSite(pages), cache).fetch_jobs()
    assert [job.company_name for job in jobs] == ["Abound"]


JOBS_AC_UK_ADVERT = (
    '<script type="application/ld+json">'
    + json.dumps(
        {
            "@type": "JobPosting",
            "title": "Data Scientist, ARC",
            "datePosted": "2026-11-06T00:00:00+00:00",
            "validThrough": "2026-11-22T00:00:00+00:00",
            "hiringOrganization": {"name": "The Alan Turing Institute"},
            "jobLocation": [{"address": {"addressLocality": "London", "addressCountry": "United Kingdom"}}],
        }
    )
    + "</script>"
)


def jobs_ac_uk_search(request: httpx.Request) -> str:
    # one full page of 25, then a short second page that repeats one advert
    if request.url.params["startIndex"] == "1":
        paths = [f"/job/DT{number:04d}/advert-{number}" for number in range(25)]
    else:
        paths = ["/job/DT0000/advert-0", "/job/DTO332/data-scientist-arc"]
    return "".join(f'<a href="{path}">advert</a><a href="{path}">again</a>' for path in paths)


def test_jobs_ac_uk_search_pages_lead_to_advert_pages(cache):
    pages = {"/search/": jobs_ac_uk_search, "/job/DTO332/data-scientist-arc": JOBS_AC_UK_ADVERT}
    fake_site = FakeSite(pages)
    search_settings = settings.job_search.model_copy(
        update={"role_terms": ["data scientist"], "locations": ["London"]}
    )
    source = JobsAcUkSource(
        search_settings,
        settings.job_sources.jobs_ac_uk.model_copy(update={"pages_per_search": 3}),
        HttpClient(settings.http, httpx.MockTransport(fake_site)),
        cache,
    )
    [job] = source.fetch_jobs()

    # 26 distinct adverts; the 25 that 404 are skipped, the short page ends the search
    assert fake_site.paths.count("/search/") == 2
    assert len([path for path in fake_site.paths if path.startswith("/job/")]) == 26
    assert job.source_job_id == "DTO332"
    assert job.closes_at.isoformat() == "2026-11-22T00:00:00+00:00"
    assert job.url == "https://www.jobs.ac.uk/job/DTO332/data-scientist-arc"
