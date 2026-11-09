from typing import Any

from job_hunt.common.settings_schema import GoogleJobsSourceSettings, LinkedInSourceSettings
from job_hunt.job_sources.job_boards.jobspy_source import JobBoardQuery, JobSpySource
from job_hunt.job_sources.job_source import register_job_source


@register_job_source
class IndeedSource(JobSpySource):
    name = "indeed"
    jobspy_site_name = "indeed"


@register_job_source
class LinkedInSource(JobSpySource):
    name = "linkedin"
    jobspy_site_name = "linkedin"
    _source_settings: LinkedInSourceSettings

    def _build_scrape_arguments(self, query: JobBoardQuery) -> dict[str, Any]:
        # without this LinkedIn results come back with no advert text
        return super()._build_scrape_arguments(query) | {
            "linkedin_fetch_description": self._source_settings.fetch_descriptions
        }


@register_job_source
class GlassdoorSource(JobSpySource):
    name = "glassdoor"
    jobspy_site_name = "glassdoor"


@register_job_source
class GoogleJobsSource(JobSpySource):
    name = "google_jobs"
    jobspy_site_name = "google"
    _source_settings: GoogleJobsSourceSettings

    # Google Jobs ignores search_term and location; it takes one sentence, the
    # way you'd type it into Google
    def _build_scrape_arguments(self, query: JobBoardQuery) -> dict[str, Any]:
        arguments = super()._build_scrape_arguments(query)
        del arguments["search_term"]
        arguments["google_search_term"] = self._source_settings.search_term_template.format(
            role=query.role_term, location=query.location
        )
        return arguments
