"""The committed keep and drop terms, on the user's examples and on real
titles from the database (Nov 2026)."""

import pytest

from job_hunt.common.settings import settings
from job_hunt.job_pipeline.title_rules import TitleRules

RULES = TitleRules(settings.title_rules)


@pytest.mark.parametrize(
    "title",
    [
        "Python Developer",
        "BI Developer",
        "Marketing Data Analyst",
        "ML Platform Engineer",
        "Quant Trader",
        "AI Consultant",
        "Research Engineer",
        "Applied Scientist, Prime Video",
        "Data Science & AI Graduate Scheme (Leeds)",
        "Software Engineer - Machine Learning",
        "Associate Data Scientist",
    ],
)
def test_relevant_titles_are_always_kept(title):
    assert RULES.rule(title) == "keep"


@pytest.mark.parametrize(
    "title",
    [
        "Computational Biologist",
        "Software Engineer",
        "Senior Software Engineer, Secure Build",
        "Sales Associate",
        "Pharmacist - 30 hours",
        "Tax Graduate - London - September 2027",
        "Graduate Rail Civil Engineer - Swindon (2027 start)",
        "Enterprise Account Executive",
        "Product Manager - Payments",
        "Site Reliability Engineer (London)",
    ],
)
def test_other_trades_are_dropped_for_free(title):
    assert RULES.rule(title) == "drop"


@pytest.mark.parametrize(
    "title", ["Forward Deployed Engineer, Athena", "Member of Technical Staff Intern", "Staff Engineer"]
)
def test_titles_rules_cant_settle_go_to_the_llm(title):
    assert RULES.rule(title) is None
