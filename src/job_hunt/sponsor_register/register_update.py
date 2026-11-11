from dataclasses import dataclass
from datetime import date

from job_hunt.common.http_client import HttpClient
from job_hunt.job_store.sponsor_repository import SponsorRepository
from job_hunt.sponsor_register.register_file import find_current_register, read_register


@dataclass(frozen=True)
class RegisterUpdate:
    register_date: date
    # None when the register loaded was already current and nothing changed
    entries_loaded: int | None


# Loads the register the Home Office currently publishes, unless it's the
# one already loaded. HttpRequestError / RegisterNotFoundError reach the caller.
def update_sponsor_register(
    http_client: HttpClient, repository: SponsorRepository, force: bool = False
) -> RegisterUpdate:
    register = find_current_register(http_client)
    if register.register_date == repository.register_date() and not force:
        return RegisterUpdate(register.register_date, None)
    sponsors = read_register(http_client.get_text(register.url), register.register_date)
    return RegisterUpdate(register.register_date, repository.replace_register(sponsors))
