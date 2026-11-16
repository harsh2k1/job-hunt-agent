from datetime import datetime, timezone

from job_hunt.common.settings import settings
from job_hunt.job_pipeline.final_verdict import UNRELATED, final_verdict
from job_hunt.job_pipeline.kind_refinement import KindRefiner, KindReply
from job_hunt.job_pipeline.listings import Listing
from job_hunt.job_store.models import ListingScreening, StoredJob
from job_hunt.job_store.screening_repository import ScreeningRepository
from job_hunt.llm.llm_client import LlmClient

NOW = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)
# what the model answers, by listing title
ANSWERS = {
    "Data Scientist - AI Trainer - Freelance": ("freelance", "data science", True),
    "Corporate Lawyer - AI Trainer - Freelance": ("freelance", "corporate law", False),
    "PhD Studentship in Machine Learning": ("studentship", "machine learning", True),
    "Research Fellow in NLP": ("employed", "NLP", True),
}


def listing(key: str, title: str) -> Listing:
    job = StoredJob(
        source="adzuna", source_job_id=key, title=title, company_name="Acme", location="London",
        url=f"https://example.com/{key}", description=f"{title}. Remote.",
        first_seen_at=NOW, last_seen_at=NOW,
    )  # fmt: skip
    return Listing(key, (job,))


def stored(key: str, kind: str) -> ListingScreening:
    return ListingScreening(
        listing_key=key, content_hash="h", fit="apply", reason="", sponsorship="not stated", model="haiku",
        kind=kind, part_time=True, starts_when_free="not stated", role_field="AI training",
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
            engagement, expertise, related = ANSWERS[title]
            answers.append(
                {"id": listing_id, "engagement": engagement, "expertise": expertise, "related": related}
            )
        return KindReply.model_validate({"listings": answers})


def test_only_other_kinds_are_refined_and_judged_by_the_expertise_asked_for(database):
    repository = ScreeningRepository(database)
    keys = {"gig": "Data Scientist - AI Trainer - Freelance",
            "law": "Corporate Lawyer - AI Trainer - Freelance",
            "phd": "PhD Studentship in Machine Learning", "fellow": "Research Fellow in NLP",
            "grad": "Graduate Data Scientist"}  # fmt: skip
    repository.save([stored(key, "graduate" if key == "grad" else "other") for key in keys])
    llm = FakeLlm()

    report = KindRefiner(settings.llm_screening, llm, repository).refine(
        [listing(key, title) for key, title in keys.items()]
    )

    # the graduate role was already settled, so it isn't asked about
    assert sorted(llm.asked) == sorted(ANSWERS) and report.asked == 4
    found = repository.find(keys)
    assert {key: found[key].kind for key in keys} == {
        "gig": "freelance", "law": "freelance", "phd": "studentship", "fellow": "full-time",
        "grad": "graduate",
    }  # fmt: skip
    verdicts = {key: final_verdict(found[key], "exact", "June 2029") for key in keys}
    # the lawyer gig isn't related: shown nowhere; the data-science gig is related but freelance: Skipped
    assert verdicts["law"].verdict == UNRELATED and "corporate law" in verdicts["law"].reason
    assert verdicts["gig"].reason == (
        "Not applicable: freelance or contract work, which your student visa doesn't allow"
    )
    assert verdicts["phd"].reason == "Not applicable: studentship, not a job"
    # a part-time fellowship is open now
    assert verdicts["fellow"].verdict == "apply"
