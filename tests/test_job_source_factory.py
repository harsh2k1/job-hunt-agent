from job_hunt.common.settings import settings
from job_hunt.common.settings_schema import JobSourcesSettings
from job_hunt.job_sources.ats_boards.ats_board_source import AtsBoardSource
from job_hunt.job_sources.job_source_factory import JobSourceFactory


def test_every_source_has_a_settings_block_and_every_block_a_source():
    assert set(JobSourceFactory.registered_source_names()) == set(JobSourcesSettings.model_fields)


def test_disabled_sources_are_left_out_unless_named():
    enabled_names = {source.name for source in JobSourceFactory.create_sources()}
    assert "glassdoor" not in enabled_names
    assert [source.name for source in JobSourceFactory.create_sources(["glassdoor"])] == ["glassdoor"]


def test_sources_that_add_companies_run_before_sources_that_read_them():
    sources = JobSourceFactory.create_sources(["greenhouse", "indeed", "ashby", "linkedin"])
    reads_companies = [isinstance(source, AtsBoardSource) for source in sources]
    assert reads_companies == sorted(reads_companies)


def test_created_source_is_configured_from_settings():
    indeed = JobSourceFactory.create("indeed", settings)
    assert indeed.name == "indeed"
