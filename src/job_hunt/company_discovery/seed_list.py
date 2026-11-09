from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from job_hunt.job_store.models import AtsPlatform, DiscoveredCompany

SEED_LIST_SOURCE = "seed list"


class SeedListEntry(BaseModel):
    # a typo in the hand-edited file should fail loudly, not be skipped
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    ats: AtsPlatform
    slug: str = Field(min_length=1)


SEED_LIST_ADAPTER = TypeAdapter(list[SeedListEntry])


def load_seed_companies(seed_list_path: Path) -> list[DiscoveredCompany]:
    entries = SEED_LIST_ADAPTER.validate_python(yaml.safe_load(seed_list_path.read_text()) or [])
    return [
        DiscoveredCompany(
            name=entry.name,
            ats_platform=entry.ats,
            ats_board_slug=entry.slug,
            discovered_via=SEED_LIST_SOURCE,
        )
        for entry in entries
    ]
