import pytest

from job_hunt.job_sources.job_posting_markup import (
    find_job_posting_markup,
    is_remote_posting,
    read_employment_type,
    read_location_text,
    read_organisation_name,
    read_salary_text,
)

# trimmed from a jobs.ac.uk advert page (Nov 2026)
JOBS_AC_UK_MARKUP = {
    "@type": "JobPosting",
    "title": "Data Scientist, ARC",
    "employmentType": "Full Time,Permanent",
    "hiringOrganization": {"@type": "Organization", "name": "The Alan Turing Institute"},
    "jobLocation": [
        {
            "@type": "Place",
            "address": {
                "addressLocality": "London",
                "addressRegion": "England",
                "addressCountry": "United Kingdom",
            },
        }
    ],
    "baseSalary": {
        "currency": "GBP",
        "value": {"unitText": "YEAR", "minValue": "46415.00", "maxValue": "52494.00"},
    },
}


def page_with(*script_bodies: str) -> str:
    scripts = "".join(f'<script type="application/ld+json">{body}</script>' for body in script_bodies)
    return f"<html><head>{scripts}</head><body>advert</body></html>"


def test_job_posting_is_found_among_other_linked_data():
    page = page_with(
        '{"@type": "BreadcrumbList"}', '[{"@type": "WebPage"}, {"@type": "JobPosting", "title": "A"}]'
    )
    assert find_job_posting_markup(page) == {"@type": "JobPosting", "title": "A"}


def test_job_posting_inside_a_graph_is_found():
    page = page_with('{"@graph": [{"@type": ["JobPosting"], "title": "B"}]}')
    assert find_job_posting_markup(page)["title"] == "B"


def test_broken_json_and_pages_without_markup_give_nothing():
    assert find_job_posting_markup(page_with("{not json")) is None
    assert find_job_posting_markup("<html></html>") is None


def test_raw_newlines_inside_strings_are_tolerated():
    page = page_with('{"@type": "JobPosting", "description": "line one\nline two"}')
    assert find_job_posting_markup(page)["description"] == "line one\nline two"


def test_fields_read_from_real_markup():
    assert read_organisation_name(JOBS_AC_UK_MARKUP) == "The Alan Turing Institute"
    assert read_location_text(JOBS_AC_UK_MARKUP) == "London, England, United Kingdom"
    assert read_salary_text(JOBS_AC_UK_MARKUP) == "GBP 46415-52494 yearly"
    assert read_employment_type(JOBS_AC_UK_MARKUP) == "Full Time,Permanent"
    assert is_remote_posting(JOBS_AC_UK_MARKUP) is None


def test_country_codes_are_spelled_out_so_location_keywords_match():
    markup = {"jobLocation": {"address": {"addressLocality": "London", "addressCountry": "GB"}}}
    assert read_location_text(markup) == "London, United Kingdom"


@pytest.mark.parametrize(
    "markup, expected",
    [
        ({"baseSalary": {"currency": "GBP", "value": 30000, "unitText": "YEAR"}}, "GBP 30000 yearly"),
        ({"baseSalary": {"currency": "GBP", "value": {"value": "550", "unitText": "DAY"}}}, "GBP 550 daily"),
        ({"baseSalary": {"currency": "GBP", "value": {"minValue": "n/a"}}}, None),
        ({}, None),
    ],
)
def test_salary_shapes(markup, expected):
    assert read_salary_text(markup) == expected


def test_remote_and_list_shaped_fields():
    markup = {
        "jobLocationType": "TELECOMMUTE",
        "employmentType": ["FULL_TIME", "INTERN"],
        "hiringOrganization": "Acme",
    }
    assert is_remote_posting(markup) is True
    assert read_employment_type(markup) == "FULL_TIME, INTERN"
    assert read_organisation_name(markup) == "Acme"
