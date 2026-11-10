import pytest

from job_hunt.job_sources.salary_text import format_salary_range


@pytest.mark.parametrize(
    "currency, low, high, interval, expected",
    [
        ("GBP", 32000, 38000, "yearly", "GBP 32000-38000 yearly"),
        ("GBP", 38000.0, None, None, "GBP 38000"),
        ("GBP", 40000, 40000, "yearly", "GBP 40000 yearly"),
        # Recruitee and schema.org markup send amounts as text
        ("GBP", "50000.00", "65000", "year", "GBP 50000-65000 year"),
        ("GBP", "n/a", None, None, None),
        ("GBP", 0, 0, None, None),
        (None, None, None, None, None),
    ],
)
def test_salary_wording(currency, low, high, interval, expected):
    assert format_salary_range(currency, low, high, interval) == expected
