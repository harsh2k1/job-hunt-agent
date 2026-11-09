import pytest
from pydantic import ValidationError

from job_hunt.common.settings import settings
from job_hunt.company_discovery.seed_list import SEED_LIST_SOURCE, load_seed_companies
from job_hunt.job_store.models import AtsPlatform


def test_seed_list_entries_become_companies(tmp_path):
    seed_list = tmp_path / "seed.yaml"
    seed_list.write_text("- name: Perplexity\n  ats: ashby\n  slug: Perplexity\n")
    [company] = load_seed_companies(seed_list)

    assert (company.name, company.ats_platform, company.ats_board_slug) == (
        "Perplexity",
        AtsPlatform.ASHBY,
        "perplexity",
    )
    assert company.discovered_via == SEED_LIST_SOURCE


def test_misspelled_field_in_the_seed_list_fails_loudly(tmp_path):
    seed_list = tmp_path / "seed.yaml"
    seed_list.write_text("- name: Perplexity\n  ats: ashby\n  slgu: perplexity\n")
    with pytest.raises(ValidationError):
        load_seed_companies(seed_list)


def test_committed_seed_list_loads_without_duplicates():
    companies = load_seed_companies(settings.companies.seed_list_path)
    boards = [(company.ats_platform, company.ats_board_slug) for company in companies]
    assert companies
    assert len(boards) == len(set(boards))
