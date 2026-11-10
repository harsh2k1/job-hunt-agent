"""Reads schema.org JobPosting markup, the JSON that job sites embed in each
advert page so Google for Jobs can list it. It's a published standard, so
one reader serves every site that has it.
"""

import json
import re
from typing import Any

from job_hunt.job_sources.location_matching import join_locations
from job_hunt.job_sources.salary_text import format_salary_range

JobPostingMarkup = dict[str, Any]

LINKED_DATA_SCRIPT_PATTERN = re.compile(
    r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL
)
SALARY_INTERVALS = {"YEAR": "yearly", "MONTH": "monthly", "WEEK": "weekly", "DAY": "daily", "HOUR": "hourly"}
# sites often give the ISO code; spelled out so location keywords match it
COUNTRY_NAMES = {"GB": "United Kingdom", "UK": "United Kingdom"}


def find_job_posting_markup(page_html: str) -> JobPostingMarkup | None:
    for script_body in LINKED_DATA_SCRIPT_PATTERN.findall(page_html):
        try:
            # strict=False: sites leave raw newlines inside description strings
            linked_data = json.loads(script_body, strict=False)
        except json.JSONDecodeError:
            continue
        job_posting = _first_job_posting(linked_data)
        if job_posting is not None:
            return job_posting
    return None


# A page can hold one object, a list of them, or an @graph of them.
def _first_job_posting(linked_data: Any) -> JobPostingMarkup | None:
    if isinstance(linked_data, list):
        return next(filter(None, map(_first_job_posting, linked_data)), None)
    if not isinstance(linked_data, dict):
        return None
    types = linked_data.get("@type")
    if types == "JobPosting" or (isinstance(types, list) and "JobPosting" in types):
        return linked_data
    return _first_job_posting(linked_data.get("@graph"))


def read_organisation_name(job_posting: JobPostingMarkup) -> str | None:
    organisation = job_posting.get("hiringOrganization")
    name = organisation.get("name") if isinstance(organisation, dict) else organisation
    return name.strip() if isinstance(name, str) and name.strip() else None


def read_locations(job_posting: JobPostingMarkup) -> list[str]:
    places = job_posting.get("jobLocation") or []
    places = places if isinstance(places, list) else [places]
    return [location for location in map(_format_place, places) if location]


def _format_place(place: Any) -> str | None:
    address = place.get("address") if isinstance(place, dict) else None
    if isinstance(address, str):
        return address.strip() or None
    if not isinstance(address, dict):
        return None
    country = address.get("addressCountry")
    if isinstance(country, dict):
        country = country.get("name")
    parts = [
        address.get("addressLocality"),
        address.get("addressRegion"),
        COUNTRY_NAMES.get(country, country),
    ]
    return (
        ", ".join(dict.fromkeys(part.strip() for part in parts if isinstance(part, str) and part.strip()))
        or None
    )


def read_location_text(job_posting: JobPostingMarkup) -> str | None:
    return join_locations(read_locations(job_posting))


def is_remote_posting(job_posting: JobPostingMarkup) -> bool | None:
    return True if job_posting.get("jobLocationType") == "TELECOMMUTE" else None


def read_employment_type(job_posting: JobPostingMarkup) -> str | None:
    employment_type = job_posting.get("employmentType")
    if isinstance(employment_type, list):
        employment_type = ", ".join(part for part in employment_type if isinstance(part, str))
    return employment_type or None


def read_salary_text(job_posting: JobPostingMarkup) -> str | None:
    salary = job_posting.get("baseSalary")
    if not isinstance(salary, dict):
        return None
    value = salary.get("value")
    if not isinstance(value, dict):
        value = {"value": value}
    low = value.get("minValue", value.get("value"))
    high = value.get("maxValue")
    interval = SALARY_INTERVALS.get(str(value.get("unitText") or salary.get("unitText") or "").upper())
    return format_salary_range(salary.get("currency"), low, high, interval)
