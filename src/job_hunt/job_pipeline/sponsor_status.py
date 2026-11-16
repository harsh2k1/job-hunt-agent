from job_hunt.job_store.sponsor_repository import SponsorRepository


class SponsorStatusLookup:
    """exact / possible / not found, once per employer name per sync; blank
    when no register is loaded."""

    def __init__(self, repository: SponsorRepository):
        self._repository = repository
        self._register_loaded = repository.register_date() is not None
        self._by_name: dict[str, str] = {}

    def __call__(self, company_name: str) -> str:
        if not self._register_loaded:
            return ""
        if company_name not in self._by_name:
            matches = self._repository.find_sponsors(company_name)
            self._by_name[company_name] = (
                "exact"
                if any(match.is_exact for match in matches)
                else "possible"
                if matches
                else "not found"
            )
        return self._by_name[company_name]
