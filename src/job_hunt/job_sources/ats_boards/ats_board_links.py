from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

from job_hunt.job_sources.ats_boards.ats_board_source import AtsBoardSource
from job_hunt.job_sources.job_source import job_source_registry
from job_hunt.job_store.models import AtsPlatform


class AtsBoardLink(BaseModel):
    # frozen so links can be collected in sets and used as dict keys
    model_config = ConfigDict(frozen=True)

    ats_platform: AtsPlatform
    ats_board_slug: str


def registered_ats_board_sources() -> list[type[AtsBoardSource]]:
    registered_classes = (job_source_registry.get(name) for name in job_source_registry.names())
    return [source_class for source_class in registered_classes if issubclass(source_class, AtsBoardSource)]


# Which company board a URL belongs to, e.g.
# https://jobs.ashbyhq.com/perplexity/1234 -> (ashby, "perplexity").
# Each ATS source recognises its own URLs, so a new ATS needs no change here.
def find_ats_board(url: str) -> AtsBoardLink | None:
    parsed_url = urlsplit(url.strip())
    for source_class in registered_ats_board_sources():
        board_slug = source_class.board_slug_from_url(parsed_url)
        if board_slug:
            return AtsBoardLink(ats_platform=source_class.ats_platform, ats_board_slug=board_slug.lower())
    return None
