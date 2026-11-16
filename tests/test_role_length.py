from datetime import datetime, timezone

from job_hunt.common.settings import settings
from job_hunt.job_pipeline.listings import Listing
from job_hunt.job_pipeline.role_length import RoleEndReply, RoleLengthReader
from job_hunt.job_store.models import ListingScreening, StoredJob
from job_hunt.job_store.screening_repository import ScreeningRepository
from job_hunt.llm.llm_client import LlmClient

NOW = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)


def listing(key: str, advert: str) -> Listing:
    job = StoredJob(
        source="workday", source_job_id=key, title="Data Science Graduate Scheme", company_name="Lloyds",
        location="Leeds", url=f"https://example.com/{key}", description=advert,
        first_seen_at=NOW, last_seen_at=NOW,
    )  # fmt: skip
    return Listing(key, (job,))


def stored(key: str, sponsorship: str, kind: str = "graduate", fit: str = "apply") -> ListingScreening:
    return ListingScreening(
        listing_key=key, content_hash="h", fit=fit, reason="", sponsorship=sponsorship, model="haiku",
        kind=kind, part_time=False,
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
            listing_id = line[1 : line.index("]")]
            self.asked.append(listing_id)
            answers.append({"id": listing_id, "role_end": "by" if "12-month" in line else "after"})
        return RoleEndReply.model_validate({"listings": answers})


def test_length_is_asked_only_where_sponsorship_decides_and_is_stored(database):
    repository = ScreeningRepository(database)
    repository.save(
        [
            stored("one-year", "not offered"),
            stored("two-year", "not offered"),
            # sponsorship doesn't decide these, so their length isn't asked
            stored("sponsors", "offered"),
            stored("intern", "not offered", kind="internship"),
            stored("unrelated", "not offered", fit="skip"),
        ]
    )
    listings = [
        listing("one-year", "A 12-month programme starting September 2027. We cannot sponsor visas."),
        listing("two-year", "A 2-year graduate scheme. We cannot sponsor visas."),
        listing("sponsors", "A graduate scheme. We sponsor visas."),
        listing("intern", "A summer internship."),
        listing("unrelated", "Sales."),
    ]
    llm = FakeLlm()
    report = RoleLengthReader(settings.llm_screening, llm, repository).read(listings, lambda company: "exact")

    assert sorted(llm.asked) == ["0", "1"]
    assert (report.asked, report.ending_in_time) == (2, 1)
    found = repository.find(["one-year", "two-year", "sponsors"])
    assert (found["one-year"].role_end, found["two-year"].role_end, found["sponsors"].role_end) == (
        "by",
        "after",
        None,
    )
