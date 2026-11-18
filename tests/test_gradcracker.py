from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from job_hunt.common.settings import settings
from job_hunt.job_pipeline.job_fetch_run import JobFetchRun
from job_hunt.job_sources.saved_pages.gradcracker import (
    GradcrackerSource,
    folder_date,
    parse_deadline,
    read_result_cards,
)
from job_hunt.job_store.company_repository import CompanyRepository
from job_hunt.job_store.job_repository import JobRepository
from job_hunt.job_store.models import FetchedJob
from job_hunt.job_store.source_run_repository import SourceRunRepository

# cards cut from a real result page saved on 17-11-2026
PAGE = (Path(__file__).parent / "fixtures" / "gradcracker_results.html").read_text()
SAVED_ON = date(2026, 11, 17)
UK = ZoneInfo("Europe/London")


def test_real_cards_are_read_and_the_hidden_decoy_is_not():
    cards = {card.job_id: card for card in read_result_cards(PAGE, SAVED_ON)}
    assert set(cards) == {"87878", "87587", "81578", "87082", "85141"}
    assert not any("9000001" in card.url for card in cards.values())
    bid_writer = cards["87878"]
    assert (bid_writer.title, bid_writer.employer, bid_writer.opportunity) == (
        "Graduate Bid Writer",
        "Morgan Sindall Infrastructure",
        "Graduate job",
    )
    assert bid_writer.url == (
        "https://www.gradcracker.com/hub/1030/morgan-sindall-infrastructure/graduate-job/87878/graduate-bid-writer"
    )
    assert bid_writer.summary == "Analytics, Data, Maths, Statistics, Economics."
    assert bid_writer.facts == {
        "Deadline": "December 13th, 2026",
        "Salary": "£33,597.20",
        "Location": "Coventry and Manchester",
        "Degree required": "2:1 and above",
        "Starting": "September 2027",
    }
    assert cards["87587"].opportunity == "Placement or internship"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("December 13th, 2026", datetime(2026, 12, 13, 23, 59, tzinfo=UK)),
        ("January 1st, 2027", datetime(2027, 1, 1, 23, 59, tzinfo=UK)),
        ("12pm, December 11th, 2026", datetime(2026, 12, 11, 12, 0, tzinfo=UK)),
        ("5:30pm, November 28th, 2026", datetime(2026, 11, 28, 17, 30, tzinfo=UK)),
        # relative to the day the page was saved, not the day it's read
        ("Today", datetime(2026, 11, 17, 23, 59, tzinfo=UK)),
        ("Tomorrow", datetime(2026, 11, 18, 23, 59, tzinfo=UK)),
        ("Ongoing", None),
        (None, None),
    ],
)
def test_deadlines_are_read_as_the_card_words_them(text, expected):
    assert parse_deadline(text, SAVED_ON) == expected


def test_day_folders_are_named_for_the_day():
    assert folder_date(Path("17-11-2026")) == date(2026, 11, 17)
    assert folder_date(Path("2026-11-18")) == date(2026, 11, 18)
    assert folder_date(Path("old pages")) is None


def make_source(tmp_path: Path, folders: dict[str, str]) -> GradcrackerSource:
    for name, page in folders.items():
        (tmp_path / name).mkdir()
        (tmp_path / name / "Grad-DS-Page1.html").write_text(page)
        # the same job on the AI page of that day counts once
        (tmp_path / name / "Grad-AI-Page1.html").write_text(page)
    return GradcrackerSource(settings.job_sources.gradcracker.model_copy(update={"pages_dir": tmp_path}))


def test_each_day_folder_is_a_batch_seen_on_its_day_and_webinars_are_left_out(tmp_path):
    source = make_source(tmp_path, {"18-11-2026": PAGE, "17-11-2026": PAGE, "notes": ""})
    batches = source.fetch_job_batches()
    assert [seen_at for seen_at, _ in batches] == [
        datetime(2026, 11, 17, 12, tzinfo=UK),
        datetime(2026, 11, 18, 12, tzinfo=UK),
    ]
    jobs = {job.source_job_id: job for job in batches[0][1]}
    # four roles; the CERN webinar isn't one
    assert set(jobs) == {"87878", "87587", "87082", "85141"}
    siemens = jobs["87082"]
    assert siemens.closes_at == datetime(2026, 11, 17, 23, 59, tzinfo=timezone.utc)
    assert siemens.description.startswith("Gradcracker listing: Placement or internship\n")
    assert "Deadline: Today" in siemens.description
    assert jobs["87878"].salary_text == "£33,597.20"


def test_a_job_is_first_and_last_seen_on_the_days_its_pages_were_saved(database, tmp_path):
    later_page = PAGE.replace("87878", "99999")
    source = make_source(tmp_path, {"17-11-2026": PAGE, "18-11-2026": later_page})
    repository = JobRepository(database)
    run = JobFetchRun([source], repository, CompanyRepository(database), SourceRunRepository(database))
    report = run.run()[0]
    # 87878 on the 17th only, 99999 on the 18th only, the other three on both
    assert (report.fetched_jobs, report.new_jobs) == (5, 5)
    since = datetime(2026, 1, 1, tzinfo=UK)
    stored = {job.source_job_id: job for job in repository.list_jobs_seen_since(since)}
    # midday UK time in November is midday UTC
    assert stored["87878"].last_seen_at == datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
    assert stored["87587"].first_seen_at == datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
    assert stored["87587"].last_seen_at == datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
    # a rerun reads the same folders and changes nothing
    assert run.run()[0].new_jobs == 0
    rerun = {job.source_job_id: job.last_seen_at for job in repository.list_jobs_seen_since(since)}
    assert rerun == {key: job.last_seen_at for key, job in stored.items()}


def gradcracker_job(job_id: str, closes_at: datetime | None) -> FetchedJob:
    return FetchedJob(
        source="gradcracker", source_job_id=job_id, title="Graduate Data Scientist", company_name="Arup",
        url=f"https://example.com/{job_id}", closes_at=closes_at,
    )  # fmt: skip


def test_a_saved_job_stays_listed_until_its_deadline_though_no_later_page_shows_it(database):
    repository = JobRepository(database)
    saved = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
    repository.save_fetched_jobs(
        [
            gradcracker_job("open", datetime(2026, 12, 20, tzinfo=timezone.utc)),
            gradcracker_job("closed", datetime(2026, 11, 30, tzinfo=timezone.utc)),
            gradcracker_job("ongoing", None),
        ],
        saved,
    )
    now = datetime(2026, 12, 10, tzinfo=timezone.utc)
    window = now - timedelta(days=14)
    listed = {job.source_job_id for job in repository.list_jobs_seen_since(window)}
    assert listed == set()
    listed = {
        job.source_job_id
        for job in repository.list_jobs_seen_since(window, {"gradcracker": now - timedelta(days=60)}, now)
    }
    # past its deadline it goes; an ongoing one stays for 60 days after it was last shown
    assert listed == {"open", "ongoing"}
    much_later = datetime(2027, 2, 1, tzinfo=timezone.utc)
    listed = {
        job.source_job_id
        for job in repository.list_jobs_seen_since(
            much_later - timedelta(days=14), {"gradcracker": much_later - timedelta(days=60)}, much_later
        )
    }
    assert listed == set()
