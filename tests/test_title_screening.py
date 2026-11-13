from datetime import datetime, timedelta, timezone

from job_hunt.common.settings import settings
from job_hunt.job_pipeline.live_listings import live_listings
from job_hunt.job_pipeline.title_screening import TitleReply, TitleScreener
from job_hunt.job_store.job_repository import JobRepository
from job_hunt.job_store.models import FetchedJob
from job_hunt.job_store.title_screening_repository import TitleScreeningRepository
from job_hunt.llm.llm_client import LlmClient

UNRELATED_WORDS = ("pharmacist", "barista", "sales associate")


class FakeLlm(LlmClient):
    name = "fake"

    def __init__(self):
        self.calls: list[list[str]] = []

    @classmethod
    def from_settings(cls, llm_settings):
        raise NotImplementedError

    def ask(self, prompt, reply_schema, system_prompt, model):
        titles = [line.split(". ", 1)[1] for line in prompt.splitlines()[1:]]
        self.calls.append(titles)
        return TitleReply.model_validate(
            {
                "titles": [
                    {"id": number, "related": not any(word in title for word in UNRELATED_WORDS)}
                    for number, title in enumerate(titles)
                ]
            }
        )


def screener(database, llm, **overrides) -> TitleScreener:
    screening_settings = settings.llm_screening.model_copy(update={"title_batch_size": 2, **overrides})
    return TitleScreener(screening_settings, llm, TitleScreeningRepository(database))


def test_each_title_is_asked_once_across_companies_and_runs(database):
    llm = FakeLlm()
    titles = ["Data Scientist", "data  scientist", "Barista", "Pharmacist", "Data Scientist"]
    report = screener(database, llm).screen(titles)
    screener(database, llm).screen(titles)
    assert [title for batch in llm.calls for title in batch] == ["data scientist", "barista", "pharmacist"]
    assert (report.titles_asked, report.unrelated) == (3, 2)


def test_a_changed_question_asks_again(database):
    llm = FakeLlm()
    screener(database, llm).screen(["Data Scientist"])
    screener(database, llm, relevant_fields=["data science", "robotics"]).screen(["Data Scientist"])
    assert len(llm.calls) == 2


def job(job_id: str, title: str, closes_in_days: int | None = None) -> FetchedJob:
    now = datetime.now(timezone.utc)
    return FetchedJob(
        source="greenhouse", source_job_id=job_id, title=title, company_name=f"Company {job_id}",
        location="London", url=f"https://example.com/{job_id}",
        closes_at=now + timedelta(days=closes_in_days) if closes_in_days is not None else None,
    )  # fmt: skip


def test_live_listings_drop_only_closed_and_unrelated_titles(database):
    JobRepository(database).save_fetched_jobs(
        [
            job("1", "Data Scientist"),
            job("2", "Barista"),
            job("3", "ML Engineer", closes_in_days=-1),
            job("4", "Quant Dev"),
        ]
    )
    titles = TitleScreeningRepository(database)
    screener(database, FakeLlm()).screen(["Data Scientist", "Barista"])

    live = live_listings(settings, JobRepository(database), titles, datetime.now(timezone.utc))

    # "Quant Dev" hasn't been asked about yet, so it stays
    assert sorted(listing.primary.title for listing in live.kept) == ["Data Scientist", "Quant Dev"]
    assert sorted((listing.primary.title, reason) for listing, reason in live.skipped) == [
        ("Barista", "Not a related role (by title)"),
        ("ML Engineer", "Closing date has passed"),
    ]


def test_the_llm_answer_counts_only_for_titles_the_rules_cant_settle(database):
    JobRepository(database).save_fetched_jobs(
        [job("1", "Staff Engineer"), job("2", "Forward Deployed Engineer")]
    )
    titles = TitleScreeningRepository(database)
    screener(database, FakeLlm()).screen(["Forward Deployed Engineer"])
    live = live_listings(settings, JobRepository(database), titles, datetime.now(timezone.utc))
    # both are LLM territory; "Staff Engineer" hasn't been asked, so it stays
    assert sorted(listing.primary.title for listing in live.kept) == [
        "Forward Deployed Engineer",
        "Staff Engineer",
    ]
