import gzip
import json

import httpx
import pytest

from job_hunt.common.http_client import HttpClient
from job_hunt.common.settings import settings
from job_hunt.company_discovery.common_crawl import url_index
from job_hunt.company_discovery.common_crawl.url_index import (
    CommonCrawlUrlIndex,
    find_latest_crawl_ids,
    surt_prefixes,
)

CRAWL_ID = "CC-MAIN-2026-43"

# URLs in SURT order, as the index keeps them, three to a block
CRAWLED_URLS = [
    ("com,acme)/careers", "https://acme.com/careers"),
    ("com,ashbyhq,jobs)/anthropic", "https://jobs.ashbyhq.com/anthropic"),
    ("com,ashbyhq,jobs)/perplexity", "https://jobs.ashbyhq.com/perplexity"),
    ("com,ashbyhq,jobs)/perplexity/123", "https://jobs.ashbyhq.com/perplexity/123"),
    ("com,ashbyhq,jobs)/wayve", "https://jobs.ashbyhq.com/wayve"),
    ("com,bbc)/news", "https://www.bbc.com/news"),
    ("com,teamtailor)/en/pricing", "https://www.teamtailor.com/en/pricing"),
    ("com,teamtailor,creditspring)/jobs", "https://creditspring.teamtailor.com/jobs"),
    ("com,teamtailor,propertyfinder)/jobs/1", "https://propertyfinder.teamtailor.com/jobs/1"),
    ("org,wikipedia)/", "https://wikipedia.org/"),
]


def build_published_index(urls_per_block: int = 3) -> dict[str, bytes]:
    """cluster.idx plus one shard, laid out the way Common Crawl publishes them."""
    shard = b""
    cluster_lines = []
    for block_number, start in enumerate(range(0, len(CRAWLED_URLS), urls_per_block)):
        block_urls = CRAWLED_URLS[start : start + urls_per_block]
        records = "".join(
            f"{surt} 20261010000000 {json.dumps({'url': url, 'status': '200'})}\n" for surt, url in block_urls
        )
        block = gzip.compress(records.encode())
        cluster_lines.append(
            f"{block_urls[0][0]} 20261010000000\tcdx-00000.gz\t{len(shard)}\t{len(block)}\t{block_number}"
        )
        shard += block
    return {"cluster.idx": ("\n".join(cluster_lines) + "\n").encode(), "cdx-00000.gz": shard}


class PublishedFiles:
    def __init__(self, files: dict[str, bytes]):
        self.requests: list[httpx.Request] = []
        self._files = files

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "index.commoncrawl.org":
            return httpx.Response(
                200, json=[{"id": CRAWL_ID}, {"id": "CC-MAIN-2026-39"}, {"id": "CC-MAIN-2026-34"}]
            )
        data = self._files[request.url.path.rsplit("/", 1)[-1]]
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": str(len(data))})
        first, last = map(int, request.headers["Range"].removeprefix("bytes=").split("-"))
        return httpx.Response(206, content=data[first : last + 1])


def make_index(files: PublishedFiles) -> CommonCrawlUrlIndex:
    return CommonCrawlUrlIndex(HttpClient(settings.http, httpx.MockTransport(files)), CRAWL_ID)


# small windows, so the binary search and the line-by-line reading really
# cross window edges instead of reading the whole file at once
@pytest.fixture(autouse=True)
def small_windows(monkeypatch):
    monkeypatch.setattr(url_index, "WINDOW_BYTES", 64)


def test_urls_on_a_host_are_listed_across_blocks():
    urls = list(make_index(PublishedFiles(build_published_index())).urls_under("com,ashbyhq,jobs)/"))
    assert urls == [
        "https://jobs.ashbyhq.com/anthropic",
        "https://jobs.ashbyhq.com/perplexity",
        "https://jobs.ashbyhq.com/perplexity/123",
        "https://jobs.ashbyhq.com/wayve",
    ]


def test_subdomains_are_listed_without_the_host_itself():
    urls = list(make_index(PublishedFiles(build_published_index())).urls_under("com,teamtailor,"))
    assert urls == [
        "https://creditspring.teamtailor.com/jobs",
        "https://propertyfinder.teamtailor.com/jobs/1",
    ]


def test_a_host_inside_one_block_is_found_in_that_block():
    urls = list(make_index(PublishedFiles(build_published_index(urls_per_block=10))).urls_under("com,bbc)/"))
    assert urls == ["https://www.bbc.com/news"]


def test_a_host_that_was_never_crawled_gives_nothing():
    assert list(make_index(PublishedFiles(build_published_index())).urls_under("com,nowhere)/")) == []


def test_only_the_blocks_that_can_hold_the_host_are_downloaded():
    files = PublishedFiles(build_published_index())
    list(make_index(files).urls_under("org,wikipedia)/"))
    shard_reads = [request for request in files.requests if request.url.path.endswith("cdx-00000.gz")]
    # of four blocks: the one starting with the host, and the one before it,
    # which can end with earlier captures of the same URL
    assert len(shard_reads) == 2


def test_domains_become_prefixes_for_the_host_and_its_subdomains():
    assert surt_prefixes("jobs.ashbyhq.com") == ("com,ashbyhq,jobs)/", "com,ashbyhq,jobs,")


def test_latest_crawls_come_newest_first():
    http_client = HttpClient(settings.http, httpx.MockTransport(PublishedFiles({})))
    assert find_latest_crawl_ids(http_client, 2) == [CRAWL_ID, "CC-MAIN-2026-39"]
