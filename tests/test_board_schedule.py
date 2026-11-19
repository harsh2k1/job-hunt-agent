from datetime import date, timedelta

from job_hunt.job_sources.ats_boards.board_schedule import BoardSchedule, board_of


def schedule(today: date) -> BoardSchedule:
    quiet = {f"quiet{number}" for number in range(70)}
    return BoardSchedule(
        active_boards={"monzo"}, known_boards={"monzo"} | quiet, quiet_every_days=7, today=today
    )


def test_a_board_with_a_role_in_the_candidates_fields_is_read_every_day():
    assert all(schedule(date(2026, 11, 19) + timedelta(days=day)).is_due("monzo") for day in range(14))


def test_each_quiet_board_is_read_once_a_week_and_each_day_reads_a_share():
    week = [schedule(date(2026, 11, 19) + timedelta(days=day)) for day in range(7)]
    for number in range(70):
        assert sum(day.is_due(f"quiet{number}") for day in week) == 1
    shares = [sum(day.is_due(f"quiet{number}") for number in range(70)) for day in week]
    assert sum(shares) == 70 and max(shares) < 70


def test_a_board_not_read_yet_is_read():
    assert schedule(date(2026, 11, 19)).is_due("newly-found-board")


def test_a_jobs_board_is_its_id_up_to_the_last_colon():
    assert board_of("accelya.wd103/careers:JR102660") == "accelya.wd103/careers"
    assert board_of("10xteam:07e8767c-cb11-41e2-9e5c-10bc7dd9e5a8") == "10xteam"
