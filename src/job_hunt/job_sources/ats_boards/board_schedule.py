"""Which tracked company boards a run reads.

Most tracked boards came from discovery and never list a role in the
candidate's fields (a fintech's sales and engineering board), yet each is
one request a run on a host that's asked once a second: Ashby's 650 boards
took 11 minutes a day, 400 of them for nothing. A board that has had a role
the sheet would show (kept by title, not judged unrelated from its advert)
is read every run; the others ("quiet") every quiet_every_days days, a
share of them each day, so a new relevant role on one is still found
within that many days. A board with no jobs stored yet is read: it hasn't
been judged."""

import hashlib
from datetime import date

from job_hunt.common.settings_schema import Settings
from job_hunt.job_store.job_repository import JobRepository


class BoardSchedule:
    def __init__(self, active_boards: set[str], known_boards: set[str], quiet_every_days: int, today: date):
        self._active_boards = active_boards
        self._known_boards = known_boards
        self._quiet_every_days = quiet_every_days
        self._today = today

    @classmethod
    def for_source(cls, settings: Settings, source_name: str, today: date | None = None) -> "BoardSchedule":
        # imported here: the pipeline imports the job sources
        from job_hunt.job_pipeline.listing_interest import cards_the_sheet_would_show

        jobs = JobRepository().list_jobs_from(source_name)
        known = {board_of(job.source_job_id) for job in jobs}
        active = {board_of(job.source_job_id) for job in cards_the_sheet_would_show(settings, jobs)}
        return cls(active, known, settings.daily_run.quiet_board_every_days, today or date.today())

    def is_due(self, board_slug: str) -> bool:
        if board_slug in self._active_boards or board_slug not in self._known_boards:
            return True
        # quiet boards spread over the days, the same share on the same day each cycle
        spread = int(hashlib.sha1(board_slug.encode()).hexdigest(), 16) % self._quiet_every_days
        return spread == self._today.toordinal() % self._quiet_every_days


# "accelya.wd103/careers:JR102660" -> "accelya.wd103/careers"
def board_of(source_job_id: str) -> str:
    return source_job_id.rsplit(":", 1)[0]
