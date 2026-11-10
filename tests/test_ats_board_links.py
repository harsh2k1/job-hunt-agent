import pytest

from job_hunt.job_sources.ats_boards.ats_board_links import (
    AtsBoardLink,
    choose_employer_link,
    find_ats_board,
    registered_ats_board_sources,
)
from job_hunt.job_store.models import AtsPlatform


@pytest.mark.parametrize(
    "url, platform, slug",
    [
        ("https://boards.greenhouse.io/wayve/jobs/123", AtsPlatform.GREENHOUSE, "wayve"),
        ("https://job-boards.greenhouse.io/anthropic/jobs/5456619008", AtsPlatform.GREENHOUSE, "anthropic"),
        ("https://job-boards.eu.greenhouse.io/synthesia", AtsPlatform.GREENHOUSE, "synthesia"),
        ("https://boards.greenhouse.io/embed/job_board?for=monzo&b=x", AtsPlatform.GREENHOUSE, "monzo"),
        ("https://jobs.lever.co/palantir/6ed76ce8", AtsPlatform.LEVER, "palantir"),
        ("https://jobs.eu.lever.co/mistral/abc/apply", AtsPlatform.LEVER_EU, "mistral"),
        ("https://jobs.ashbyhq.com/Perplexity/d5bc2302", AtsPlatform.ASHBY, "perplexity"),
        ("https://apply.workable.com/huggingface/j/81B46579FE/", AtsPlatform.WORKABLE, "huggingface"),
        (
            "https://jobs.smartrecruiters.com/BoschGroup/744000160675673",
            AtsPlatform.SMARTRECRUITERS,
            "boschgroup",
        ),
    ],
)
def test_board_is_found_from_any_link_to_it(url, platform, slug):
    assert find_ats_board(url) == AtsBoardLink(ats_platform=platform, ats_board_slug=slug)


@pytest.mark.parametrize(
    "url",
    [
        "https://careers.bbc.co.uk/job/Data-Scientist",
        "https://uk.indeed.com/viewjob?jk=c97f46cd24e3190a",
        # a Workable job link with no company slug in it
        "https://apply.workable.com/j/81B46579FE",
        "https://jobs.lever.co/",
        "not a url",
    ],
)
def test_links_that_arent_a_known_board_give_none(url):
    assert find_ats_board(url) is None


def test_every_ats_platform_with_a_reader_is_recognised():
    assert {source.ats_platform for source in registered_ats_board_sources()} == {
        AtsPlatform.GREENHOUSE,
        AtsPlatform.LEVER,
        AtsPlatform.LEVER_EU,
        AtsPlatform.ASHBY,
        AtsPlatform.WORKABLE,
        AtsPlatform.SMARTRECRUITERS,
    }


def test_first_link_is_used_when_none_is_an_ats_board():
    assert (
        choose_employer_link(["https://uk.indeed.com/1", "https://careers.bbc.co.uk/2"])
        == "https://uk.indeed.com/1"
    )
    assert choose_employer_link([]) is None


def test_google_tracking_params_are_dropped_from_apply_links():
    link = "https://jobs.ashbyhq.com/marshmallow/abc?src=x&utm_source=google_jobs_apply&utm_medium=organic"
    assert choose_employer_link([link]) == "https://jobs.ashbyhq.com/marshmallow/abc?src=x"
