import pytest

from job_hunt.job_store.company_repository import CompanyNotFoundError, CompanyRepository
from job_hunt.job_store.models import AtsPlatform, DiscoveredCompany


def make_company(slug: str, platform: AtsPlatform = AtsPlatform.ASHBY) -> DiscoveredCompany:
    return DiscoveredCompany(
        name=slug.title(), ats_platform=platform, ats_board_slug=slug, discovered_via="seed list"
    )


def test_company_is_only_added_once_per_board(database):
    repository = CompanyRepository(database)
    assert repository.add_company_if_new(make_company("perplexity")) is True
    assert repository.add_company_if_new(make_company("perplexity")) is False
    # same slug on another platform is a different board
    assert repository.add_company_if_new(make_company("perplexity", AtsPlatform.GREENHOUSE)) is True


def test_deactivated_company_is_not_listed_or_re_added(database):
    repository = CompanyRepository(database)
    repository.add_company_if_new(make_company("monzo"))
    repository.add_company_if_new(make_company("wayve"))
    repository.deactivate_company(AtsPlatform.ASHBY, "monzo")

    assert [company.ats_board_slug for company in repository.list_active_companies()] == ["wayve"]
    assert repository.add_company_if_new(make_company("monzo")) is False


def test_listing_can_be_filtered_by_platform(database):
    repository = CompanyRepository(database)
    repository.add_company_if_new(make_company("wayve", AtsPlatform.GREENHOUSE))
    repository.add_company_if_new(make_company("perplexity", AtsPlatform.ASHBY))

    greenhouse_companies = repository.list_active_companies(AtsPlatform.GREENHOUSE)
    assert [company.ats_board_slug for company in greenhouse_companies] == ["wayve"]


def test_deactivating_an_unknown_company_is_an_error(database):
    with pytest.raises(CompanyNotFoundError):
        CompanyRepository(database).deactivate_company(AtsPlatform.LEVER, "nobody")
