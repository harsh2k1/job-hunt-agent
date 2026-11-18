"""The scheduler's files in the state folder: the schedule the user set,
the record of slots run, and the PATH a run needs (what worked in the
terminal where the schedule was set; schedulers start jobs with a bare
one). Written atomically (temp file, then rename), so a check that dies
midway never leaves a half-written file behind."""

import json
import os
from pathlib import Path
from typing import Any

from job_hunt.scheduling.schedule_spec import ScheduleSpec
from job_hunt.scheduling.slots import SlotHistory

SPEC_FILE = "schedule.json"
HISTORY_FILE = "history.json"
RUN_PATH_FILE = "run_path.json"
LAST_RUN_FILE = "last_run.json"


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
        return SlotHistory(claimed=set(data.get("claimed", [])), attempts=dict(data.get("attempts", {})))

    def save_history(self, history: SlotHistory) -> None:
        self.write_json(HISTORY_FILE, {"claimed": sorted(history.claimed), "attempts": history.attempts})

    # the latest run: slots, started, finished (None while running), outcome, summary
    def load_last_run(self) -> dict[str, Any] | None:
        return self.read_json(LAST_RUN_FILE)

    def save_last_run(self, last_run: dict[str, Any]) -> None:
        self.write_json(LAST_RUN_FILE, last_run)

    # folders put first on the run's PATH
    def load_run_path(self) -> list[str]:
        return list(self.read_json(RUN_PATH_FILE) or [])

    def save_run_path(self, folders: list[str]) -> None:
        self.write_json(RUN_PATH_FILE, folders)

    def read_json(self, name: str) -> Any:
        path = self.state_dir / name
        return json.loads(path.read_text()) if path.exists() else None

    def write_json(self, name: str, data: Any) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.state_dir / name
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2))
        os.replace(temporary, path)
