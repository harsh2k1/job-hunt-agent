import re
from urllib.parse import urljoin, urlsplit

from job_hunt.common.http_client import HttpRequestError
from job_hunt.common.logger import get_logger
from job_hunt.common.settings_schema import JobsAcUkSourceSettings
from job_hunt.job_sources.advert_pages.advert_page_source import AdvertPage, AdvertPageLink, AdvertPageSource
from job_hunt.job_sources.job_boards.job_board_query import JobBoardQuery, build_job_board_queries
from job_hunt.job_sources.job_source import JobSourceError, register_job_source

logger = get_logger(__name__)

SITE_URL = "https://www.jobs.ac.uk"
SEARCH_URL = f"{SITE_URL}/search/"
SEARCH_PAGE_SIZE = 25
# /job/DTO332/data-scientist-arc: the code is the advert's id
ADVERT_PATH_PATTERN = re.compile(r"href=\"(/job/([A-Z0-9]+)/[^\"?#]+)\"")


@register_job_source
class JobsAcUkSource(AdvertPageSource):
    """jobs.ac.uk: university and research-institute jobs (research
    assistants, research engineers, data scientists), from employers that
    nearly all hold a sponsor licence.

    Its own search finds the adverts for each role term and location; each
    advert page then gives the full JobPosting markup, closing date included.
    """

    name = "jobs_ac_uk"
    _source_settings: JobsAcUkSourceSettings

    def _list_advert_links(self) -> list[AdvertPageLink]:
        queries = build_job_board_queries(self._search_settings)
        links_by_url: dict[str, AdvertPageLink] = {}
        failed_queries = 0
        for query in queries:
            try:
                advert_paths = self._search(query)
            except HttpRequestError as error:
                failed_queries += 1
                logger.warning(
                    "%s: search for %r in %s failed: %s", self.name, query.role_term, query.location, error
                )
                continue
            logger.info(
                "%s: %d adverts for %r in %s", self.name, len(advert_paths), query.role_term, query.location
            )
            for path in advert_paths:
                url = urljoin(SITE_URL, path)
                links_by_url.setdefault(url, AdvertPageLink(url))
        if failed_queries == len(queries):
            raise JobSourceError(f"{self.name}: every one of {len(queries)} searches failed")
        return list(links_by_url.values())

    def _search(self, query: JobBoardQuery) -> list[str]:
        advert_paths: list[str] = []
        for page_index in range(self._source_settings.pages_per_search):
            page_paths = self._search_page(query, page_index)
            advert_paths.extend(path for path in page_paths if path not in advert_paths)
            if len(page_paths) < SEARCH_PAGE_SIZE:
                break
        return advert_paths

    def _search_page(self, query: JobBoardQuery, page_index: int) -> list[str]:
        params = {
            "keywords": query.role_term,
            "location": query.location,
            "pageSize": SEARCH_PAGE_SIZE,
            # counts from 1
            "startIndex": page_index * SEARCH_PAGE_SIZE + 1,
        }
        return self._cache.get_or_compute(
            f"{self.name}:search:{query.location}:{query.role_term}:{page_index}",
            lambda: list(
                dict.fromkeys(
                    path
                    for path, _ in ADVERT_PATH_PATTERN.findall(
                        self._http_client.get_text(SEARCH_URL, params=params)
                    )
                )
            ),
            ttl_hours=self._source_settings.search_cache_ttl_hours,
        )

    def _source_job_id(self, advert_page: AdvertPage) -> str:
        return urlsplit(advert_page.url).path.split("/")[2]
