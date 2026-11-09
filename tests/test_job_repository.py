from datetime import datetime, timedelta, timezone

from job_hunt.job_store.job_repository import LOOKUP_BATCH_SIZE, UPDATABLE_FIELDS, JobRepository
from job_hunt.job_store.models import FetchedJob


def make_fetched_job(source_job_id: str = "123", title: str = "Graduate Data Scientist") -> FetchedJob:
    return FetchedJob(
        source="greenhouse",
        source_job_id=source_job_id,
        title=title,
        company_name="Example Ltd",
        location="London",
        url=f"https://example.com/jobs/{source_job_id}",
    )


def test_new_and_repeated_jobs_are_counted_separately(database):
    repository = JobRepository(database)
    first_run = repository.save_fetched_jobs([make_fetched_job("1"), make_fetched_job("2")])
    second_run = repository.save_fetched_jobs([make_fetched_job("2"), make_fetched_job("3")])

    assert (first_run.new_jobs, first_run.already_known_jobs) == (2, 0)
    assert (second_run.new_jobs, second_run.already_known_jobs) == (1, 1)
    assert repository.count_jobs_by_source() == {"greenhouse": 3}


def test_repeat_sighting_updates_fields_but_keeps_first_seen(database):
    repository = JobRepository(database)
    monday = datetime(2026, 11, 9, 8, tzinfo=timezone.utc)
    tuesday = monday + timedelta(days=1)
    repository.save_fetched_jobs([make_fetched_job(title="Data Scientist")], seen_at=monday)
    repository.save_fetched_jobs([make_fetched_job(title="Data Scientist (Graduate)")], seen_at=tuesday)

    [stored_job] = repository.list_jobs_first_seen_since(monday)
    assert stored_job.title == "Data Scientist (Graduate)"
    assert stored_job.first_seen_at == monday
    assert stored_job.last_seen_at == tuesday


def test_same_job_twice_in_one_batch_is_stored_once_with_the_later_copy(database):
    repository = JobRepository(database)
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    summary = repository.save_fetched_jobs(
        [make_fetched_job("1", title="Data Scientist"), make_fetched_job("1", title="ML Engineer")]
    )

    assert (summary.new_jobs, summary.already_known_jobs) == (1, 0)
    assert [job.title for job in repository.list_jobs_first_seen_since(since)] == ["ML Engineer"]


def test_batches_larger_than_one_lookup_are_matched(database):
    repository = JobRepository(database)
    job_count = LOOKUP_BATCH_SIZE * 2 + 1
    repository.save_fetched_jobs(make_fetched_job(str(index)) for index in range(job_count))
    summary = repository.save_fetched_jobs(make_fetched_job(str(index)) for index in range(job_count))
    assert (summary.new_jobs, summary.already_known_jobs) == (0, job_count)


def test_every_fetched_field_except_identity_is_updated_on_a_new_sighting():
    assert set(UPDATABLE_FIELDS) == set(FetchedJob.model_fields) - {"source", "source_job_id"}


def test_posted_at_with_timezone_is_stored_as_utc():
    london_summer_time = timezone(timedelta(hours=1))
    job = FetchedJob.model_validate(
        make_fetched_job().model_dump() | {"posted_at": datetime(2026, 7, 1, 9, tzinfo=london_summer_time)}
    )
    assert job.posted_at == datetime(2026, 7, 1, 8, tzinfo=timezone.utc)
    assert job.posted_at.tzinfo == timezone.utc


def test_posted_at_without_timezone_is_taken_as_utc():
    job = FetchedJob.model_validate(make_fetched_job().model_dump() | {"posted_at": datetime(2026, 7, 1, 9)})
    assert job.posted_at == datetime(2026, 7, 1, 9, tzinfo=timezone.utc)
