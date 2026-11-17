"""Which slots are due at a check, and the record of slots already run.

A slot is one scheduled time on one day ("2026-11-18 03:00"). Each check
looks back one day: with catch-up on (--run-on-wake), every slot in that
day not yet run is due (several missed slots are covered by one run); with
it off, only a slot the check finds within the grace period."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from job_hunt.scheduling.schedule_spec import ScheduleSpec

# how far back a missed slot still counts; a longer gap is caught up by one run
LOOK_BACK = timedelta(days=1)
# history older than this is dropped
KEEP_HISTORY = timedelta(days=7)
SLOT_FORMAT = "%Y-%m-%d %H:%M"


def slot_id(slot: datetime) -> str:
    return slot.strftime(SLOT_FORMAT)


@dataclass
class SlotHistory:
    # slots run, or running now (claimed when a run starts, so it isn't started twice)
    claimed: set[str] = field(default_factory=set)
    # failed attempts per slot
    attempts: dict[str, int] = field(default_factory=dict)

    def claim(self, slots: list[datetime]) -> None:
        self.claimed.update(slot_id(slot) for slot in slots)

    # A failed run gives its slots back for a retry, up to max_attempts each.
    def record_failure(self, slots: list[datetime], max_attempts: int) -> list[datetime]:
        released = []
        for slot in slots:
            key = slot_id(slot)
            self.attempts[key] = self.attempts.get(key, 0) + 1
            if self.attempts[key] < max_attempts:
                self.claimed.discard(key)
                released.append(slot)
        return released

    def prune(self, now: datetime) -> None:
        oldest = slot_id(now - KEEP_HISTORY)
        self.claimed = {key for key in self.claimed if key >= oldest}
        self.attempts = {key: count for key, count in self.attempts.items() if key >= oldest}


def due_slots(spec: ScheduleSpec, now: datetime, history: SlotHistory, grace: timedelta) -> list[datetime]:
    due = []
    for day in (now.date() - timedelta(days=1), now.date()):
        for moment in spec.times:
            slot = datetime.combine(day, moment)
            late_by = now - slot
            if slot > now or late_by >= LOOK_BACK or slot < spec.active_since:
                continue
            if slot_id(slot) in history.claimed:
                continue
            if spec.catch_up or late_by <= grace:
                due.append(slot)
    return due
