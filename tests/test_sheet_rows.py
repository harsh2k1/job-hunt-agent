from datetime import datetime, timedelta, timezone

from job_hunt.job_pipeline.final_verdict import NOT_SCREENED, FinalVerdict
from job_hunt.job_pipeline.listings import Listing
from job_hunt.job_store.models import StoredJob
from job_hunt.sheet_sync.sheet_rows import (
    KEY_COLUMN,
    ScreenedListing,
    build_jobs_rows,
    build_skipped_rows,
    jobs_columns,
    merge_user_cells,
    read_user_cells,
)

NOW = datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc)
NO_SCREENING = lambda listing: None  # noqa: E731
NO_VERDICT = lambda listing: FinalVerdict(NOT_SCREENED)  # noqa: E731
COLUMNS = jobs_columns(lambda company: "exact", NO_SCREENING, NO_VERDICT)


def listing(key: str, title: str, closes_in_days: int | None = None) -> Listing:
    job = StoredJob(
        source="greenhouse", source_job_id=key, title=title, company_name="Wise", location="London",
        url=f"https://example.com/{key}", first_seen_at=NOW, last_seen_at=NOW,
        closes_at=NOW + timedelta(days=closes_in_days) if closes_in_days is not None else None,
    )  # fmt: skip
    return Listing(key, (job,))


def jobs_rows(listings, user_cells=None):
    return build_jobs_rows(listings, COLUMNS, user_cells or read_user_cells([]), NO_VERDICT)


def test_jobs_tab_has_numbered_rows_in_a_fixed_column_order():
    rows = jobs_rows(
        [listing("a", "No deadline"), listing("b", "Closes later", 20), listing("c", "Closes soon", 3)]
    )
    header = rows[0]
    assert header == [
        "#", "Role", "Company", "Status", "Advert link", "Why it's valid", "JD", "Location", "Kind", "Type",
        "Found on", "Found by", "Sponsorship in advert", "Sponsor licence", "Closes", "Start", "Posted",
        "First fetched", "Salary", "Verdict", "Why", "Notes", KEY_COLUMN,
    ]  # fmt: skip
    assert header[-1] == KEY_COLUMN
    assert [row[0] for row in rows[1:]] == ["1", "2", "3"]
    assert [row[header.index("Role")] for row in rows[1:]] == ["Closes soon", "Closes later", "No deadline"]
    assert rows[1][header.index("Verdict")] == "not screened yet"
    assert rows[1][header.index("Found by")] == "agent"


def test_the_users_cells_and_own_rows_survive_even_a_new_column_order():
    # the sheet as an older layout left it: different order, the user's edits in it
    old_layout = [
        ["Title", "Company", "Status", "Notes", "Found by", KEY_COLUMN],
        ["Data Scientist", "Wise", "Applied", "Referral from Sam", "agent", "a"],
        ["Friend's referral at Revolut", "Revolut", "To apply", "", "self", ""],
    ]
    rows = jobs_rows(
        [listing("a", "Data Scientist", 5), listing("b", "ML Engineer", 9)], read_user_cells(old_layout)
    )
    header = rows[0]
    status, notes = header.index("Status"), header.index("Notes")
    assert (rows[1][status], rows[1][notes]) == ("Applied", "Referral from Sam")
    assert rows[2][status] == ""
    own = rows[3]
    assert (own[0], own[header.index("Company")], own[status], own[header.index("Found by")]) == (
        "3",
        "Revolut",
        "To apply",
        "self",
    )


def test_skipped_rows_are_numbered_and_carry_the_reason():
    rows = build_skipped_rows([ScreenedListing(listing("a", "Barista"), "title isn't a data / AI role")])
    assert rows[0][-2:] == ["Skip reason", KEY_COLUMN]
    assert rows[1][-2] == "title isn't a data / AI role"


def test_notes_follow_a_listing_between_jobs_and_to_check():
    jobs = read_user_cells([["Role", "Status", "Found by", KEY_COLUMN], ["Mine", "To apply", "self", ""]])
    checks = read_user_cells(
        [["Role", "Status", "Notes", KEY_COLUMN], ["Data Scientist", "Applied", "via Sam", "a"]]
    )
    merged = merge_user_cells(jobs, checks)
    assert merged.by_key["a"]["Notes"] == "via Sam"
    assert [row["Role"] for row in merged.own_rows] == ["Mine"]
