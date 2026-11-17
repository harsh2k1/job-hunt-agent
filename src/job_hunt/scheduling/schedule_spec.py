"""What the user chose with `job-hunt schedule set`: when the run happens
each day, and whether a run missed while the computer slept still happens. Kept in the state folder and read
by every check, so changing the times needs nothing else reinstalled."""

import re
from datetime import datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# only daily for now; a weekly schedule would add a value here and a rule in slots.py
Frequency = Literal["daily"]
FREQUENCIES: tuple[Frequency, ...] = ("daily",)

_CLOCK_TIME = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", re.IGNORECASE)


class ScheduleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    frequency: Frequency = "daily"
    # local wall-clock times, sorted, no repeats
    times: tuple[time, ...] = Field(min_length=1)
    # --run-on-wake: a slot missed while the computer slept or was off runs
    # as soon as it's back; otherwise it's skipped
    catch_up: bool
    # when the schedule was set: earlier slots never count as missed
    active_since: datetime

    @field_validator("times")
    @classmethod
    def tidy(cls, value: tuple[time, ...]) -> tuple[time, ...]:
        return tuple(sorted({moment.replace(second=0, microsecond=0) for moment in value}))

    def describe(self) -> str:
        return f"{self.frequency} at " + ", ".join(moment.strftime("%H:%M") for moment in self.times)


class InvalidTimeError(ValueError):
    pass


# "03:00", "3:00", "3am", "9pm", "9:30pm", "21:00"
def parse_clock_time(text: str) -> time:
    match = _CLOCK_TIME.match(text.strip())
    if not match:
        raise InvalidTimeError(f"{text!r} isn't a time; use e.g. 03:00, 21:30, 3am or 9:30pm")
    hour, minute, meridiem = int(match.group(1)), int(match.group(2) or 0), (match.group(3) or "").lower()
    if meridiem:
        if not 1 <= hour <= 12:
            raise InvalidTimeError(f"{text!r}: with am/pm the hour is 1-12")
        hour = hour % 12 + (12 if meridiem == "pm" else 0)
    if hour > 23 or minute > 59:
        raise InvalidTimeError(f"{text!r} isn't a time of day")
    return time(hour, minute)


# n runs a day spread evenly from the first, e.g. 3 from 03:00 -> 03:00, 11:00, 19:00
def evenly_spaced(times_per_day: int, first: time) -> tuple[time, ...]:
    if not 1 <= times_per_day <= 24:
        raise InvalidTimeError("times per day must be 1-24")
    start = first.hour * 60 + first.minute
    minutes = (start + round(index * 24 * 60 / times_per_day) for index in range(times_per_day))
    return tuple(time((total // 60) % 24, total % 60) for total in minutes)
