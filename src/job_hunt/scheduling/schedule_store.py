"""The scheduler's files in the state folder: the schedule the user set and
the record of slots run. Written atomically (temp file, then rename), so a
check that dies midway never leaves a half-written file behind."""

import json
import os
from pathlib import Path
from typing import Any

from job_hunt.scheduling.schedule_spec import ScheduleSpec
from job_hunt.scheduling.slots import SlotHistory

SPEC_FILE = "schedule.json"
HISTORY_FILE = "history.json"


class ScheduleStore:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir

    def load_spec(self) -> ScheduleSpec | None:
        data = self.read_json(SPEC_FILE)
        return ScheduleSpec.model_validate(data) if data is not None else None

    def save_spec(self, spec: ScheduleSpec) -> None:
        self.write_json(SPEC_FILE, spec.model_dump(mode="json"))

    def remove_spec(self) -> None:
        (self.state_dir / SPEC_FILE).unlink(missing_ok=True)

    def load_history(self) -> SlotHistory:
        data = self.read_json(HISTORY_FILE) or {}
        return SlotHistory(
            claimed=set(data.get("claimed", [])),
            attempts=dict(data.get("attempts", {})),
            wake_reminder_day=data.get("wake_reminder_day"),
        )

    def save_history(self, history: SlotHistory) -> None:
        self.write_json(
            HISTORY_FILE,
            {
                "claimed": sorted(history.claimed),
                "attempts": history.attempts,
                "wake_reminder_day": history.wake_reminder_day,
            },
        )

    def read_json(self, name: str) -> Any:
        path = self.state_dir / name
        return json.loads(path.read_text()) if path.exists() else None

    def write_json(self, name: str, data: Any) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.state_dir / name
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2))
        os.replace(temporary, path)
