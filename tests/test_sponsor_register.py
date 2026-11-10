from datetime import date

import pytest

from job_hunt.job_store.company_names import normalise_company_name, split_trading_name
from job_hunt.job_store.sponsor_repository import SponsorRepository
from job_hunt.sponsor_register.register_file import read_register

# shaped like the real file: byte-order mark, cells padded with spaces, a
# trading name after "T/A", one organisation on two routes
REGISTER_CSV = (
    "﻿Organisation Name,Town/City,County,Type & Rating,Route\n"
    '" Monzo Bank Limited "," London  ",,Worker (A rating),Skilled Worker\n'
    '"ROOFOODS LTD T/A Deliveroo",London,,Worker (A rating),Skilled Worker\n'
    '"ROOFOODS LTD T/A Deliveroo",London,,Worker (A rating),'
    "Global Business Mobility: Senior or Specialist Worker\n"
    '"Data Ltd",Leeds,West Yorkshire,Worker (A rating),Skilled Worker\n'
    '"Datadog UK Ltd",London,,Worker (A rating),Skilled Worker\n'
)
REGISTER_DATE = date(2026, 11, 10)


@pytest.mark.parametrize(
    "name, normalised",
    [
        ("The Monzo Bank Ltd.", "monzo bank"),
        ("ROOFOODS LTD", "roofoods"),
        ("Marks & Spencer PLC", "marks and spencer"),
        ("Datadog UK Ltd", "datadog"),
        ("Bending Spoons", "bending spoons"),
        # "Group" says which firm it is, so it stays
        ("Wise Payments Group Limited", "wise payments group"),
        ("The Company", "the"),
    ],
)
def test_company_names_get_one_spelling(name, normalised):
    assert normalise_company_name(name) == normalised


def test_trading_name_is_split_from_the_legal_name():
    assert split_trading_name("ROOFOODS LTD T/A Deliveroo") == ("ROOFOODS LTD", "Deliveroo")
    assert split_trading_name("Monzo Bank Limited") == ("Monzo Bank Limited", None)


def test_register_rows_become_sponsors():
    sponsors = read_register(REGISTER_CSV, REGISTER_DATE)
    monzo, deliveroo = sponsors[0], sponsors[1]

    assert len(sponsors) == 5
    assert (monzo.organisation_name, monzo.town_city, monzo.normalised_name) == (
        "Monzo Bank Limited",
        "London",
        "monzo bank",
    )
    assert (deliveroo.trading_name, deliveroo.normalised_trading_name) == ("Deliveroo", "deliveroo")
    assert monzo.register_date == REGISTER_DATE


@pytest.fixture
def repository(database) -> SponsorRepository:
    repository = SponsorRepository(database)
    repository.replace_register(read_register(REGISTER_CSV, REGISTER_DATE))
    return repository


def test_brand_name_finds_the_legal_name_as_a_possible_match(repository):
    [match] = repository.find_sponsors("Monzo")
    assert match.sponsor.organisation_name == "Monzo Bank Limited"
    assert not match.is_exact


def test_trading_name_is_an_exact_match(repository):
    matches = repository.find_sponsors("Deliveroo")
    assert {match.sponsor.route for match in matches} == {
        "Skilled Worker",
        "Global Business Mobility: Senior or Specialist Worker",
    }
    assert all(match.is_exact for match in matches)


def test_prefix_matches_stop_at_word_boundaries(repository):
    # "Data" matches Data Ltd exactly, not Datadog
    assert [match.sponsor.organisation_name for match in repository.find_sponsors("Data")] == ["Data Ltd"]
    assert repository.find_sponsors("Anthropic") == []
    assert repository.find_sponsors("Ltd") == []


def test_a_new_register_replaces_the_old_one(repository):
    newer = (
        "Organisation Name,Town/City,County,Type & Rating,Route\n"
        "Wayve Technologies Ltd,London,,Worker (A rating),Skilled Worker\n"
    )
    repository.replace_register(read_register(newer, date(2026, 11, 11)))
    assert repository.find_sponsors("Monzo") == []
    assert repository.find_sponsors("Wayve Technologies")[0].is_exact
    assert repository.register_date() == date(2026, 11, 11)
