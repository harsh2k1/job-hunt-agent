from datetime import datetime, timezone

from job_hunt.common.settings import settings
from job_hunt.job_pipeline.listings import Listing
from job_hunt.job_pipeline.llm_screening import ListingFacts, LlmScreening, ScreeningReply, decide
from job_hunt.job_store.models import StoredJob
from job_hunt.job_store.screening_repository import ScreeningRepository
from job_hunt.llm.llm_client import LlmClient, LlmError, LlmUsageLimitError

NOW = datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc)


def facts(**overrides) -> ListingFacts:
    values = dict(id="0", role_field="data science", related="yes", kind="graduate", part_time=False,
                  starts_when_free="not stated", sponsorship="not stated", start_date=None)  # fmt: skip
    return ListingFacts(**{**values, **overrides})


def test_an_unrelated_role_is_skipped_whatever_else_is_true():
    decision = decide(facts(role_field="software engineering", related="no", kind="graduate"))
    assert (decision.verdict, decision.reason) == ("skip", "Not a related role (software engineering)")


def test_a_full_time_role_not_starting_once_free_is_skipped():
    decision = decide(facts(kind="full-time", starts_when_free="not stated"))
    assert decision.verdict == "skip"
    assert decision.reason == "Not applicable: full-time role, not starting once you're free full-time"


def test_nothing_else_is_ever_a_reason_to_skip():
    # a related graduate role is valid, however senior or demanding it sounds
    decision = decide(facts(role_field="senior ML engineering", kind="graduate"))
    assert decision.verdict == "apply"
    assert decision.valid_because == "• Graduate role\n• Senior ML engineering role"


def test_valid_bullets_include_the_start_when_the_advert_gives_it():
    decision = decide(facts(kind="full-time", starts_when_free="yes", start_date="September 2027"))
    assert decision.valid_because == (
        "• Full-time role starting once you're free full-time\n• Data science role\n• Starts September 2027"
    )


def test_unclear_field_or_unclear_internship_is_checked_not_skipped():
    assert decide(facts(related="unclear")).verdict == "check"
    internship = decide(facts(kind="internship", starts_when_free="not stated"))
    assert (internship.verdict, internship.reason) == (
        "check",
        "Internship: confirm it's part-time or starts once you're free",
    )


def listing(key: str, title: str, description: str | None = "An advert.") -> Listing:
    job = StoredJob(
        source="greenhouse", source_job_id=key, title=title, company_name="Wise", location="London",
        url=f"https://example.com/{key}", description=description, first_seen_at=NOW, last_seen_at=NOW,
    )  # fmt: skip
    return Listing(key, (job,))


class FakeLlm(LlmClient):
    """Reads titles: 'Engineer' is unrelated, 'Experienced' is full-time,
    'thin' is unclear on the cheap model."""

    name = "fake"

    def __init__(self, fail_models=(), usage_limit_after: int | None = None):
        self.calls: list[tuple[str, int]] = []
        self.prompts: list[str] = []
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
        self.prompts.append(prompt)
        if model in self._fail_models:
            raise LlmError("bad reply")
        answers = []
        for line in listing_lines:
            listing_id = line.split('"id": "')[1].split('"')[0]
            related = (
                "no" if "Engineer" in line else "unclear" if "thin" in line and model == "haiku" else "yes"
            )
            kind = "full-time" if "Experienced" in line else "graduate"
            answers.append(facts(id=listing_id, related=related, kind=kind, role_field="data science"))
        return ScreeningReply(listings=answers)


def make_screening(database, llm, **overrides) -> LlmScreening:
    screening_settings = settings.llm_screening.model_copy(
        update={"first_pass_model": "haiku", "second_pass_model": "sonnet", "batch_size": 2, **overrides}
    )
    return LlmScreening(screening_settings, llm, ScreeningRepository(database))


def stored(database, key):
    return ScreeningRepository(database).find([key])[key]


def test_listings_are_read_in_batches_and_the_decision_stored(database):
    llm = FakeLlm()
    report = make_screening(database, llm).screen([listing(str(n), "Data Scientist") for n in range(3)])
    assert llm.calls == [("haiku", 2), ("haiku", 1)]
    assert (report.screened, report.verdicts) == (3, {"apply": 3})
    assert (stored(database, "0").fit, stored(database, "0").kind) == ("apply", "graduate")


def test_skips_carry_only_the_two_allowed_reasons(database):
    make_screening(database, FakeLlm()).screen(
        [listing("a", "Software Engineer"), listing("b", "Experienced Data Scientist")]
    )
    assert (stored(database, "a").fit, stored(database, "a").reason) == (
        "skip",
        "Not a related role (data science)",
    )
    assert stored(database, "b").reason.startswith("Not applicable:")


def test_a_listing_is_sent_once_until_its_advert_changes(database):
    llm = FakeLlm()
    make_screening(database, llm).screen([listing("a", "Data Scientist")])
    make_screening(database, llm).screen([listing("a", "Data Scientist")])
    assert len(llm.calls) == 1
    make_screening(database, llm).screen([listing("a", "Data Scientist", description="A rewritten advert.")])
    assert len(llm.calls) == 2


def test_only_unclear_fields_with_advert_text_go_to_the_stronger_model(database):
    llm = FakeLlm()
    listings = [
        listing("a", "Data Scientist"),
        listing("b", "thin advert"),
        listing("c", "thin advert", None),
    ]
    report = make_screening(database, llm, batch_size=10).screen(listings)
    assert llm.calls == [("haiku", 3), ("sonnet", 1)]
    assert report.sent_to_second_pass == 1
    assert (stored(database, "b").fit, stored(database, "b").model) == ("apply", "sonnet")
    assert (stored(database, "c").fit, stored(database, "c").model) == ("check", "haiku")


def test_the_cap_leaves_the_rest_for_the_next_run(database):
    report = make_screening(database, FakeLlm(), max_listings_per_run=2).screen(
        [listing(str(n), "Data Scientist") for n in range(5)]
    )
    assert (report.screened, report.deferred) == (2, 3)


def test_a_usage_limit_stops_the_run_and_keeps_each_answered_batch(database):
    report = make_screening(database, FakeLlm(usage_limit_after=1)).screen(
        [listing(str(n), "Data Scientist") for n in range(4)]
    )
    assert report.stopped_by_usage_limit
    assert set(ScreeningRepository(database).find(["0", "1", "2", "3"])) == {"0", "1"}


def test_a_failed_batch_leaves_its_listings_due(database):
    report = make_screening(database, FakeLlm(fail_models={"haiku"})).screen([listing("a", "Data Scientist")])
    assert (report.failed_batches, report.screened) == (1, 0)
    assert ScreeningRepository(database).find(["a"]) == {}


def test_sponsorship_read_by_rule_from_the_whole_advert_overrides_the_model(database):
    advert = (
        "A data science graduate scheme. "
        + "Details about the team. " * 400
        + "Note: we cannot sponsor visas."
    )
    make_screening(database, FakeLlm()).screen([listing("a", "Data Scientist", advert)])
    assert stored(database, "a").sponsorship == "not offered"


def test_the_model_is_told_what_the_rule_found_and_never_sees_a_profile(database):
    llm = FakeLlm()
    make_screening(database, llm).screen(
        [listing("a", "Data Scientist", "Graduate role. Visa sponsorship is available for this role.")]
    )
    assert '"sponsorship_in_text": "offered"' in llm.prompts[0]
    assert "Candidate" not in llm.prompts[0]
