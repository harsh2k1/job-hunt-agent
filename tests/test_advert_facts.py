"""Sponsorship wording taken verbatim from real adverts in the database."""

# the quotes stay on one line each, as the adverts wrote them
# ruff: noqa: E501

import pytest

from job_hunt.job_pipeline.advert_facts import advert_digest, sponsorship_in_advert

NOT_OFFERED = [
    # Lloyds Banking Group, Data Science & AI Graduate Scheme
    "We encourage all students to apply, from newly graduated through to MSc/PhD. Note: we cannot sponsor visas.",
    "Grayce is not on the UK Border Agency's sponsor register and is unable to sponsor work visas for international applicants.",
    "As such, applicants must have the legal right to work in the UK without requiring sponsorship now or in the future "
    "under the UK points-based immigration system.",
    "You must have the right to work in the UK for the full duration of the programme, without requiring visa sponsorship.",
    "- Eligibility: no visa sponsorship, 2:2 degree accepted",
    "Unfortunately we are unable to offer visa sponsorship for this role.",
    "Visa sponsorship is not available for this position.",
    "At the moment, this role isn’t open to visa sponsorship applications.",
    "Unfortunately we can’t sponsor visas for this role.",
    "Applicants must be legally authorized to work in the location where this position is based The position is not eligible for immigration or work-visa sponsorship.",
    "- Right to work in the UK unsponsored for the duration of the programme.",
]
OFFERED = [
    "Visa sponsorship: we provide visa sponsorship support and assess each circumstance on a case-by-case basis.",
    "Aria can support suitable international candidates through the appropriate UK visa routes.",
    "If we make you an offer, we will make every reasonable effort to get you a visa.",
    "Visa sponsorship is available for this role.",
    "We are able to sponsor Skilled Worker visas.",
    "We offer visa sponsorship for exceptional candidates.",
    "By reference to applicable SOC code for this role, sponsorship may be possible under Skilled Worker Route.",
    "*This role may be eligible at this time for visa sponsorship under the Skilled Worker visa route.",
    "✈️ Relocation support and visa sponsorship where applicable",
    "Barclays will accept applications from candidates who require visa sponsorship to work in the UK and will provide a skilled worker visa for successful candidates.",
    "Visa sponsorship: Docker considers visa sponsorship on a case-by-case basis based on business needs.",
    "- For exceptional candidates, Meridial will consider visa sponsorship and relocation support.",
    "Please be advised that due to the minimum salary thresholds imposed by the UKVI, this post may qualify for university sponsorship under the Skilled Worker route.",
    "Visa sponsorship and comprehensive relocation packages available.",
]
NOT_ABOUT_VISAS = [
    "You'll support the end-to-end management of hosted and sponsored events to drive business growth.",
    "You're a consultative salesperson who builds executive sponsorship with customers.",
    "Engage directly with strategic accounts as an executive technical sponsor.",
    "Company-sponsored sports teams and fitness events.",
    "Strategic oversight of our highest-profile third-party sponsorships across the region.",
    "Experience working within a sponsor organisation or CRO.",
    "We'll need some information, including residency status and nationality, which is required for visa processing.",
    "We are unable to support visa costs. Funding: this scholarship covers the full cost of tuition fees.",
    "Candidates must be able to demonstrate their eligibility to work in the UK in accordance with the Immigration, Asylum and Nationality Act 2006.",
    "Global Mobility: work with the Global Mobility team to manage visa and sponsorship processes for international employees.",
    "Visa sponsorship and relocation support for a move to NYC (after 2 years' tenure)",
]


@pytest.mark.parametrize("text", NOT_OFFERED)
def test_adverts_that_rule_out_sponsorship(text):
    assert sponsorship_in_advert(text) == "not offered"


@pytest.mark.parametrize("text", OFFERED)
def test_adverts_that_offer_sponsorship(text):
    assert sponsorship_in_advert(text) == "offered"


@pytest.mark.parametrize("text", NOT_ABOUT_VISAS)
def test_sponsor_mentions_that_arent_about_visas_say_nothing(text):
    assert sponsorship_in_advert(text) is None


def test_not_offered_wins_when_an_advert_says_both():
    text = "We can sponsor visas for experienced hires. We cannot sponsor visas for graduate roles."
    assert sponsorship_in_advert(text) == "not offered"


def test_an_advert_that_doesnt_say_gives_none():
    assert sponsorship_in_advert("A data science role in London working on fraud models.") is None
    assert sponsorship_in_advert(None) is None


def test_a_visa_line_deep_in_the_advert_reaches_the_digest():
    advert = (
        "About the role. "
        + "We build models for customers. " * 250
        + "Note: we cannot sponsor visas. "
        + "Benefits. " * 50
    )
    assert advert.index("cannot sponsor") > 7000
    digest = advert_digest(advert, opening_chars=1200, key_fact_chars=1500)
    assert digest.startswith("About the role.")
    assert "Note: we cannot sponsor visas." in digest
    assert len(digest) < 3000


def test_start_dates_and_experience_lines_reach_the_digest():
    advert = (
        "Intro. " * 300 + "The programme starts in September 2027. Requires 3 years of experience in Python."
    )
    digest = advert_digest(advert, opening_chars=500, key_fact_chars=1500)
    assert "starts in September 2027" in digest
    assert "3 years of experience" in digest
