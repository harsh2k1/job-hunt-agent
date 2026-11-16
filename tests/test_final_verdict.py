import pytest

from job_hunt.job_pipeline.eligibility import sponsorship_rule
from job_hunt.job_pipeline.final_verdict import final_verdict
from job_hunt.job_store.models import ListingScreening

VISA_ENDS = "June 2029"


@pytest.mark.parametrize(
    "kind, part_time, sponsorship, licence, eligible",
    [
        # graduate and full-time roles outlast the Graduate visa
        ("graduate", False, "not offered", "exact", False),
        ("full-time", False, "not offered", "exact", False),
        ("graduate", False, "not stated", "not found", None),
        ("graduate", False, "not stated", "possible", True),
        ("graduate", False, "offered", "not found", True),
        # part-time roles and internships end before it: sponsorship doesn't matter
        ("internship", False, "not offered", "not found", True),
        ("part-time", True, "not offered", "not found", True),
        ("graduate", True, "not offered", "not found", True),
    ],
)
def test_sponsorship_only_matters_for_roles_that_outlast_the_graduate_visa(
    kind, part_time, sponsorship, licence, eligible
):
    assert sponsorship_rule(kind, part_time, sponsorship, licence, VISA_ENDS).eligible is eligible


def screening(**fields) -> ListingScreening:
    values = {
        "listing_key": "k",
        "content_hash": "h",
        "fit": "apply",
        "reason": "",
        "sponsorship": "not stated",
        "model": "haiku",
        "kind": "graduate",
        "part_time": False,
        "valid_because": "• Graduate role\n• Data science role",
    }
    return ListingScreening(**{**values, **fields})


def test_lloyds_graduate_scheme_that_cant_sponsor_is_skipped():
    verdict = final_verdict(screening(sponsorship="not offered"), "exact", VISA_ENDS)
    assert verdict.verdict == "skip"
    assert (
        verdict.reason
        == "Not applicable: no visa sponsorship, and it runs past your Graduate visa (ends June 2029)"
    )


def test_an_employer_off_the_register_is_checked_not_listed():
    verdict = final_verdict(screening(), "not found", VISA_ENDS)
    assert (verdict.verdict, verdict.reason) == (
        "check",
        "No sponsor licence found under this name; confirm they can sponsor",
    )


def test_a_valid_role_says_how_sponsorship_is_covered():
    verdict = final_verdict(screening(), "exact", VISA_ENDS)
    assert verdict.verdict == "apply"
    assert verdict.valid_because.endswith("• Employer holds a sponsor licence (exact match on the register)")
    offered = final_verdict(screening(sponsorship="offered"), "not found", VISA_ENDS)
    assert offered.valid_because.endswith("• Advert offers visa sponsorship")


def test_earlier_skips_and_checks_stand():
    assert (
        final_verdict(screening(fit="skip", reason="Not a related role (sales)"), "exact", VISA_ENDS).verdict
        == "skip"
    )
    assert (
        final_verdict(screening(fit="check", reason="Internship: confirm"), "exact", VISA_ENDS).verdict
        == "check"
    )
    assert final_verdict(None, "exact", VISA_ENDS).verdict == "not screened yet"


@pytest.mark.parametrize(
    "role_end, verdict",
    [
        # a 12-month programme from September 2027 ends inside the Graduate visa
        ("by", "apply"),
        # a 2-year scheme, or a permanent role, runs past it
        ("after", "skip"),
        ("not stated", "skip"),
    ],
)
def test_whether_no_sponsorship_matters_depends_on_when_the_role_ends(role_end, verdict):
    result = final_verdict(screening(sponsorship="not offered", role_end=role_end), "exact", VISA_ENDS)
    assert result.verdict == verdict


def test_an_unstated_length_is_said_in_the_reason():
    result = final_verdict(screening(sponsorship="not offered", role_end="not stated"), "exact", VISA_ENDS)
    assert "the advert doesn't give its length" in result.reason


def test_a_role_ending_in_time_says_so_in_its_bullets():
    result = final_verdict(screening(sponsorship="not offered", role_end="by"), "not found", VISA_ENDS)
    assert result.valid_because.endswith(
        "• Ends by June 2029, within your Graduate visa: no sponsorship needed"
    )
