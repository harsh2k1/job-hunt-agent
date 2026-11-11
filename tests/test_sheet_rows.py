from datetime import datetime, timedelta, timezone

from job_hunt.common.settings import settings
from job_hunt.job_pipeline.listings import Listing
from job_hunt.job_pipeline.rule_screening import RuleScreening
from job_hunt.job_store.models import StoredJob
from job_hunt.sheet_sync.sheet_rows import (
    KEY_COLUMN,
    ScreenedListing,
    build_jobs_rows,
    build_skipped_rows,
    jobs_columns,
    read_user_cells,
)

NOW = datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc)
COLUMNS = jobs_columns(lambda company: "exact")


def listing(key: str, title: str, closes_in_days: int | None = None) -> Listing:
    job = StoredJob(
        source="greenhouse",
        source_job_id=key,
        title=title,
        company_name="Wise",
        location="London",
        url=f"https://example.com/{key}",
        closes_at=NOW + timedelta(days=closes_in_days) if closes_in_days is not None else None,
        first_seen_at=NOW,
        last_seen_at=NOW,
    )
    return Listing(key, (job,))


def test_jobs_are_ordered_by_deadline_and_say_who_found_them():
    rows = build_jobs_rows(
        [listing("a", "No deadline"), listing("b", "Closes later", 20), listing("c", "Closes soon", 3)],
        COLUMNS,
        read_user_cells([]),
    )
    header = rows[0]
    assert [row[0] for row in rows[1:]] == ["Closes soon", "Closes later", "No deadline"]
    assert header[-3:] == ["Status", "Notes", KEY_COLUMN]
    assert rows[1][header.index("Found by")] == "agent"
    assert rows[1][header.index("Found on")] == "greenhouse"


def test_the_users_status_notes_and_own_rows_survive_a_resync():
    first = build_jobs_rows([listing("a", "Data Scientist", 5)], COLUMNS, read_user_cells([]))
    header = first[0]
    first[1][header.index("Status")] = "Applied"
    own_row = [""] * len(header)
    own_row[0], own_row[header.index("Found by")] = "Friend's referral at Revolut", "self"
    first.append(own_row)

    second = build_jobs_rows(
        [listing("a", "Data Scientist", 5), listing("b", "ML Engineer", 9)], COLUMNS, read_user_cells(first)
    )
    assert second[1][header.index("Status")] == "Applied"
    assert second[2][header.index("Status")] == ""
    assert second[-1][0] == "Friend's referral at Revolut"
    assert second[-1][header.index("Found by")] == "self"


def test_skipped_rows_carry_the_reason():
    rows = build_skipped_rows([ScreenedListing(listing("a", "Barista"), "title isn't a data / AI role")])
    assert rows[1][rows[0].index("Skip reason")] == "title isn't a data / AI role"


def test_rule_screening_keeps_data_roles_and_says_why_others_go():
    screening = RuleScreening(settings.screening)
    assert screening.screen(listing("a", "Graduate Data Scientist", 10), NOW).kept
    assert screening.screen(listing("b", "Machine Learning Intern"), NOW).kept
    assert (
        screening.screen(listing("c", "Pharmacist - 30 hours"), NOW).reason == "title isn't a data / AI role"
    )
    assert screening.screen(listing("d", "Senior Data Scientist"), NOW).reason == "senior role (senior)"
    assert screening.screen(listing("e", "Data Analyst", -1), NOW).reason == "closing date has passed"
    # whole words: "Maintenance" isn't "ai", "Email" isn't "ml"
    assert not screening.screen(listing("f", "Maintenance Engineer"), NOW).kept
