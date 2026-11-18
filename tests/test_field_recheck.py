from datetime import datetime, timezone

from job_hunt.common.settings import settings
from job_hunt.job_pipeline.field_recheck import FieldRechecker, FieldReply
from job_hunt.job_pipeline.final_verdict import UNRELATED, final_verdict
from job_hunt.job_pipeline.listings import Listing
from job_hunt.job_pipeline.llm_screening import PLAUSIBLE_FIELD_REASON, UNCLEAR_FIELD_REASON
from job_hunt.job_store.models import ListingScreening, StoredJob
from job_hunt.job_store.screening_repository import ScreeningRepository
from job_hunt.llm.llm_client import LlmClient

NOW = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
# real titles from Gradcracker cards, and what the model answers
ANSWERS = {
    "2027 BNY Analyst Program - Office of the CFO": ("finance office", "no"),
    "Technical Graduate - Data Analysis & Data Science 2027": ("data analysis", "plausible"),
    "Graduate Scheme 2027": ("unknown", "plausible"),
}


def listing(key: str, title: str) -> Listing:
    job = StoredJob(
        source="gradcracker", source_job_id=key, title=title, company_name="BNY",
        url=f"https://example.com/{key}", first_seen_at=NOW, last_seen_at=NOW,
        description="Gradcracker listing: Graduate job\nComputing, Data, Maths.",
    )  # fmt: skip
    return Listing(key, (job,))


def stored(key: str, reason: str) -> ListingScreening:
    return ListingScreening(
        listing_key=key, content_hash="h", fit="check", reason=reason, sponsorship="not stated",
        model="haiku", kind="graduate", part_time=False, starts_when_free="yes", role_field="unclear",
    )  # fmt: skip


class FakeLlm(LlmClient):
    name = "fake"

    def __init__(self):
        self.asked: list[str] = []

    @classmethod
    def from_settings(cls, llm_settings):
        raise NotImplementedError

    def ask(self, prompt, reply_schema, system_prompt, model):
        answers = []
        for line in prompt.splitlines()[1:]:
            listing_id, title = line[1 : line.index("]")], line[line.index("]") + 2 : line.index(" at ")]
            self.asked.append(title)
            field, verdict = ANSWERS[title]
            answers.append({"id": listing_id, "field": field, "could_be_relevant": verdict})
        return FieldReply.model_validate({"listings": answers})


def test_only_unclear_fields_are_rechecked_and_only_a_clear_no_takes_a_listing_off(database):
    repository = ScreeningRepository(database)
    titles = {"cfo": "2027 BNY Analyst Program - Office of the CFO",
              "data": "Technical Graduate - Data Analysis & Data Science 2027",
              "vague": "Graduate Scheme 2027", "settled": "Graduate Data Scientist"}  # fmt: skip
    repository.save(
        [stored(key, UNCLEAR_FIELD_REASON) for key in ("cfo", "data", "vague")]
        + [stored("settled", "Internship: confirm it's part-time")]
    )
    llm = FakeLlm()
    report = FieldRechecker(settings.llm_screening, llm, repository).recheck(
        [listing(key, title) for key, title in titles.items()]
    )
    assert sorted(llm.asked) == sorted(ANSWERS) and report.asked == 3
    found = repository.find(titles)
    verdicts = {key: final_verdict(found[key], "exact", "June 2029") for key in titles}
    assert verdicts["cfo"].verdict == UNRELATED and "finance office" in verdicts["cfo"].reason
    # plausible ones stay on To check, never made valid
    assert (verdicts["data"].verdict, verdicts["data"].reason) == ("check", PLAUSIBLE_FIELD_REASON)
    assert verdicts["vague"].verdict == "check"
    # asked once: a second run finds nothing to ask
    FieldRechecker(settings.llm_screening, llm, repository).recheck(
        [listing(k, t) for k, t in titles.items()]
    )
    assert len(llm.asked) == 3
