from datetime import datetime, timezone

from job_hunt.common.settings import settings
from job_hunt.job_pipeline.listings import Listing
from job_hunt.job_pipeline.llm_screening import ListingVerdict, LlmScreening, ScreeningReply
from job_hunt.job_store.models import StoredJob
from job_hunt.job_store.screening_repository import ScreeningRepository
from job_hunt.llm.llm_client import LlmClient, LlmError, LlmUsageLimitError

NOW = datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc)


def listing(key: str, title: str, description: str | None = "An advert.") -> Listing:
    job = StoredJob(
        source="greenhouse", source_job_id=key, title=title, company_name="Wise", location="London",
        url=f"https://example.com/{key}", description=description, first_seen_at=NOW, last_seen_at=NOW,
    )  # fmt: skip
    return Listing(key, (job,))


class FakeLlm(LlmClient):
    """Answers by title: 'unclear' titles are undecided on the cheap model."""

    name = "fake"

    def __init__(self, fail_models=(), usage_limit_after: int | None = None):
        self.calls: list[tuple[str, int]] = []
        self._fail_models = set(fail_models)
        self._usage_limit_after = usage_limit_after

    @classmethod
    def from_settings(cls, llm_settings):
        raise NotImplementedError

    def ask(self, prompt, reply_schema, system_prompt, model):
        if self._usage_limit_after is not None and len(self.calls) >= self._usage_limit_after:
            raise LlmUsageLimitError("You've hit your usage limit; resets 5pm")
        listing_lines = [line for line in prompt.splitlines() if line.startswith('{"id"')]
        self.calls.append((model, len(listing_lines)))
        if model in self._fail_models:
            raise LlmError("bad reply")
        verdicts = []
        for line in listing_lines:
            listing_id = line.split('"id": "')[1].split('"')[0]
            fit = "check" if "unclear" in line and model == "haiku" else "strong"
            verdicts.append(
                ListingVerdict(id=listing_id, fit=fit, reason="graduate ML role", sponsorship="not stated",
                               start_date=None, experience_years=None)
            )  # fmt: skip
        return ScreeningReply(verdicts=verdicts)


def make_screening(database, llm, profile="summary: MSc Data Science", **overrides) -> LlmScreening:
    screening_settings = settings.llm_screening.model_copy(
        update={"first_pass_model": "haiku", "second_pass_model": "sonnet", "batch_size": 2, **overrides}
    )
    return LlmScreening(screening_settings, llm, ScreeningRepository(database), profile)


def test_listings_are_screened_in_batches_and_stored(database):
    llm = FakeLlm()
    report = make_screening(database, llm).screen([listing(str(n), "Data Scientist") for n in range(3)])

    assert llm.calls == [("haiku", 2), ("haiku", 1)]
    assert (report.screened, report.fits) == (3, {"strong": 3})
    assert ScreeningRepository(database).find(["0"])["0"].reason == "graduate ML role"


def test_a_listing_is_sent_once_until_its_advert_or_the_profile_changes(database):
    llm = FakeLlm()
    make_screening(database, llm).screen([listing("a", "Data Scientist")])
    make_screening(database, llm).screen([listing("a", "Data Scientist")])
    assert len(llm.calls) == 1

    make_screening(database, llm).screen([listing("a", "Data Scientist", description="A rewritten advert.")])
    make_screening(database, llm, profile="summary: MSc Data Science, also open to PhD").screen(
        [listing("a", "Data Scientist", description="A rewritten advert.")]
    )
    assert len(llm.calls) == 3


def test_only_undecided_listings_with_advert_text_go_to_the_stronger_model(database):
    llm = FakeLlm()
    listings = [
        listing("a", "Data Scientist"),
        listing("b", "unclear role"),
        listing("c", "unclear role", None),
    ]
    report = make_screening(database, llm, batch_size=10).screen(listings)

    assert llm.calls == [("haiku", 3), ("sonnet", 1)]
    assert report.sent_to_second_pass == 1
    stored = ScreeningRepository(database).find(["b", "c"])
    assert (stored["b"].fit, stored["b"].model) == ("strong", "sonnet")
    assert (stored["c"].fit, stored["c"].model) == ("check", "haiku")


def test_the_cap_leaves_the_rest_for_the_next_run(database):
    report = make_screening(database, FakeLlm(), max_listings_per_run=2).screen(
        [listing(str(n), "Data Scientist") for n in range(5)]
    )
    assert (report.screened, report.deferred) == (2, 3)


def test_a_usage_limit_stops_the_run_and_keeps_what_was_screened(database):
    llm = FakeLlm(usage_limit_after=1)
    report = make_screening(database, llm).screen([listing(str(n), "Data Scientist") for n in range(4)])
    assert report.stopped_by_usage_limit
    assert report.screened == 2


def test_a_failed_batch_leaves_its_listings_due(database):
    report = make_screening(database, FakeLlm(fail_models={"haiku"})).screen([listing("a", "Data Scientist")])
    assert (report.failed_batches, report.screened) == (1, 0)
    assert ScreeningRepository(database).find(["a"]) == {}


def test_each_batch_is_stored_as_soon_as_it_is_answered(database):
    # the second call hits the limit; the first batch is already stored
    make_screening(database, FakeLlm(usage_limit_after=1)).screen(
        [listing(str(n), "Data Scientist") for n in range(4)]
    )
    assert set(ScreeningRepository(database).find(["0", "1", "2", "3"])) == {"0", "1"}


def test_sponsorship_read_by_rule_from_the_whole_advert_overrides_the_model(database):
    # the line is far past what the model reads of the opening; the model says "not stated"
    advert = (
        "A data science graduate scheme. "
        + "Details about the team. " * 400
        + "Note: we cannot sponsor visas."
    )
    llm = FakeLlm()
    make_screening(database, llm).screen([listing("a", "Data Scientist", advert)])
    assert ScreeningRepository(database).find(["a"])["a"].sponsorship == "not offered"


def test_the_model_is_told_what_the_rule_found(database):
    prompts: list[str] = []

    class RecordingLlm(FakeLlm):
        def ask(self, prompt, reply_schema, system_prompt, model):
            prompts.append(prompt)
            return super().ask(prompt, reply_schema, system_prompt, model)

    make_screening(database, RecordingLlm()).screen(
        [listing("a", "Data Scientist", "Graduate role. Visa sponsorship is available for this role.")]
    )
    assert '"sponsorship_in_text": "offered"' in prompts[0]
