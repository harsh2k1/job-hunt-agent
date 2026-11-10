from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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


# An advert often links to several places to apply. A link to the company's
# own ATS board is the most useful one: it's the original, and it lets the
# fetch run start reading that company's whole board.
def choose_employer_link(apply_links: list[str]) -> str | None:
    apply_links = [strip_tracking_params(link) for link in apply_links]
    board_links = [link for link in apply_links if find_ats_board(link) is not None]
    return (board_links or apply_links or [None])[0]


# Job boards add utm_* to outgoing links, so the same advert would otherwise
# carry a different URL from every source that links to it.
def strip_tracking_params(url: str) -> str:
    parts = urlsplit(url)
    kept_params = [(name, value) for name, value in parse_qsl(parts.query) if not name.startswith("utm_")]
    return urlunsplit(parts._replace(query=urlencode(kept_params)))
