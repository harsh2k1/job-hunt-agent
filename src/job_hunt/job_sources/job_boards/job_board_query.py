from dataclasses import dataclass

from job_hunt.common.settings_schema import JobSearchSettings


@dataclass(frozen=True)
class JobBoardQuery:
    role_term: str
    location: str


# Job boards that take a keyword and a place are searched for every role term
# in every location.
def build_job_board_queries(search_settings: JobSearchSettings) -> list[JobBoardQuery]:
    return [
        JobBoardQuery(role_term, location)
        for location in search_settings.locations
        for role_term in search_settings.role_terms
    ]
