import math

from job_hunt.common.settings_schema import AdzunaSourceSettings
from job_hunt.job_sources.html_to_text import html_to_text
from job_hunt.job_sources.job_boards.job_board_query import JobBoardQuery
from job_hunt.job_sources.job_boards.search_api_source import ApiPosting, SearchApiSource
from job_hunt.job_sources.job_source import UNKNOWN_COMPANY_NAME, register_job_source, require_secret
from job_hunt.job_sources.salary_text import format_salary_range
from job_hunt.job_sources.timestamps import parse_iso_timestamp
from job_hunt.job_store.models import FetchedJob

SEARCH_URL = "https://api.adzuna.com/v1/api/jobs/{country_code}/search/{page_number}"
KILOMETRES_PER_MILE = 1.609
# Adzuna doesn't say the currency; it's always the country's own
CURRENCY_BY_COUNTRY_CODE = {"gb": "GBP"}


@register_job_source
class AdzunaSource(SearchApiSource):
    """Adzuna's search API. Adzuna collects adverts from many UK boards and
    agencies, so it overlaps with others but reaches smaller ones too."""

    name = "adzuna"
    display_name = "Adzuna"
    _source_settings: AdzunaSourceSettings

    def _check_credentials(self) -> None:
        self._credentials()

    def _credentials(self) -> dict[str, str]:
        return {
            "app_id": require_secret(self.name, self._source_settings.app_id, "app_id"),
            "app_key": require_secret(self.name, self._source_settings.app_key, "app_key"),
        }

    def _fetch_page(self, query: JobBoardQuery, page_index: int) -> list[ApiPosting]:
        url = SEARCH_URL.format(country_code=self._source_settings.country_code, page_number=page_index + 1)
        params = {
            "what": query.role_term,
            "where": query.location,
            "distance": round(self._search_settings.search_radius_miles * KILOMETRES_PER_MILE),
            "max_days_old": math.ceil(self._search_settings.max_posting_age_hours / 24),
            "results_per_page": self._source_settings.results_per_page,
            "sort_by": "date",
        }
        return self._http_client.get_json(url, params=params | self._credentials()).get("results", [])

    def _to_fetched_job(self, raw_posting: ApiPosting) -> FetchedJob:
        # Adzuna estimates a salary when the advert has none; only a real one is kept
        has_real_salary = str(raw_posting.get("salary_is_predicted", "0")) == "0"
        return FetchedJob(
            source=self.name,
            source_job_id=str(raw_posting["id"]),
            # search terms come back wrapped in <strong>
            title=html_to_text(raw_posting["title"]),
            company_name=(raw_posting.get("company") or {}).get("display_name") or UNKNOWN_COMPANY_NAME,
            location=(raw_posting.get("location") or {}).get("display_name"),
            url=raw_posting["redirect_url"],
            # a snippet; the full advert is only on the page it links to
            description=html_to_text(raw_posting.get("description")),
            posted_at=parse_iso_timestamp(raw_posting.get("created")),
            employment_type=format_contract(
                raw_posting.get("contract_type"), raw_posting.get("contract_time")
            ),
            salary_text=format_salary_range(
                CURRENCY_BY_COUNTRY_CODE.get(self._source_settings.country_code),
                raw_posting.get("salary_min"),
                raw_posting.get("salary_max"),
                "yearly",
            )
            if has_real_salary
            else None,
        )


# ("permanent", "full_time") -> "permanent, full-time"
def format_contract(*contract_parts: str | None) -> str | None:
    return ", ".join(part.replace("_", "-") for part in contract_parts if part) or None
