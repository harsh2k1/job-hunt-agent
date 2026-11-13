import pytest

from job_hunt.job_pipeline.eligibility import eligibility


@pytest.mark.parametrize(
    "kind, part_time, starts, eligible",
    [
        # graduate roles: yes, unless they clearly start too early
        ("graduate", False, "yes", True),
        ("graduate", False, "not stated", True),
        ("graduate", False, "no", False),
        # part-time: always
        ("part-time", True, "not stated", True),
        ("full-time", True, "no", True),
        # internships: part-time, or starting once free; unclear is checked
        ("internship", True, "no", True),
        ("internship", False, "yes", True),
        ("internship", False, "no", False),
        ("internship", False, "not stated", None),
        # an ordinary full-time job: only if it clearly starts once free
        ("full-time", False, "yes", True),
        ("full-time", False, "not stated", False),
        ("full-time", False, "no", False),
        ("other", False, "not stated", False),
    ],
)
def test_who_can_apply(kind, part_time, starts, eligible):
    assert eligibility(kind, part_time, starts).eligible is eligible


def test_a_skip_says_why():
    assert (
        eligibility("full-time", False, "not stated").reason
        == "full-time role, not starting once you're free full-time"
    )
