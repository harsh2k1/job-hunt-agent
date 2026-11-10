"""Common Crawl's URL index, read straight from its published files.

Common Crawl crawls a few billion pages a month and publishes an index of
every URL. The index server (index.commoncrawl.org) is often overloaded, so
this reads the same files it serves from, on the data host:

- cluster.idx: one line per block of ~3,000 URLs, sorted by SURT key (the
  host reversed, "com,ashbyhq,jobs)/perplexity"). About 100 MB, so it's
  binary-searched with range requests instead of downloaded.
- cdx-NNNNN.gz shards: each block is its own gzip member, fetched by range.

So listing every URL under one host takes about a dozen small requests to
find the blocks, then one request per block.
"""

import gzip
import json
from collections.abc import Iterator
from dataclasses import dataclass

from job_hunt.common.http_client import HttpClient

CRAWL_LIST_URL = "https://index.commoncrawl.org/collinfo.json"
INDEX_FILES_URL = "https://data.commoncrawl.org/cc-index/collections/{crawl_id}/indexes/{file_name}"
CLUSTER_INDEX_FILE = "cluster.idx"
# how much of cluster.idx is read per request (a few hundred lines)
WINDOW_BYTES = 64 * 1024


@dataclass(frozen=True)
class IndexBlock:
    shard: str
    offset: int
    length: int


@dataclass(frozen=True)
class ClusterIndexLine:
    # where the line starts in cluster.idx
    start_offset: int
    # SURT key of the first URL in the block
    surt: str
    block: IndexBlock


# "jobs.ashbyhq.com" -> the host itself ("com,ashbyhq,jobs)/") and every
# subdomain under it ("com,ashbyhq,jobs,"); ATSs put boards in either place.
def surt_prefixes(domain: str) -> tuple[str, str]:
    reversed_host = ",".join(reversed(domain.lower().strip(".").split(".")))
    return f"{reversed_host})/", f"{reversed_host},"


# newest first, e.g. ["CC-MAIN-2026-43", "CC-MAIN-2026-39"]
def find_latest_crawl_ids(http_client: HttpClient, count: int) -> list[str]:
    return [crawl["id"] for crawl in http_client.get_json(CRAWL_LIST_URL)[:count]]


class CommonCrawlUrlIndex:
    def __init__(self, http_client: HttpClient, crawl_id: str):
        self._http_client = http_client
        self.crawl_id = crawl_id
        self._cluster_index_url = INDEX_FILES_URL.format(crawl_id=crawl_id, file_name=CLUSTER_INDEX_FILE)
        self._cluster_index_size: int | None = None

    def urls_under(self, surt_prefix: str) -> Iterator[str]:
        for block in self._blocks_holding(surt_prefix):
            shard_url = INDEX_FILES_URL.format(crawl_id=self.crawl_id, file_name=block.shard)
            compressed = self._http_client.get_byte_range(
                shard_url, block.offset, block.offset + block.length - 1
            )
            for line in gzip.decompress(compressed).decode("utf-8", errors="replace").splitlines():
                # "<surt> <timestamp> <json>"
                surt, _, rest = line.partition(" ")
                if surt.startswith(surt_prefix):
                    yield json.loads(rest.partition(" ")[2])["url"]

    # The blocks whose URLs can start with the prefix: the last block that
    # starts before it (it may run into the prefix) and every block that
    # starts inside it.
    def _blocks_holding(self, surt_prefix: str) -> list[IndexBlock]:
        blocks: list[IndexBlock] = []
        block_before: IndexBlock | None = None
        for line in self._lines_from(self._offset_of_last_line_before(surt_prefix)):
            if line.surt.startswith(surt_prefix):
                blocks.append(line.block)
            elif line.surt < surt_prefix:
                block_before = line.block
            else:
                break
        return ([block_before] if block_before else []) + blocks

    # Binary search over byte offsets: each probe reads the first whole line
    # after a point and compares its key.
    def _offset_of_last_line_before(self, surt_prefix: str) -> int:
        low, high = 0, self._size()
        while high - low > WINDOW_BYTES:
            middle = (low + high) // 2
            line = self._first_line_after(middle)
            if line is None or line.surt >= surt_prefix:
                high = middle
            else:
                low = line.start_offset
        return low

    def _first_line_after(self, offset: int) -> ClusterIndexLine | None:
        line_start = self._next_line_start(offset)
        return None if line_start is None else next(self._lines_from(line_start), None)

    # Where the line after the one holding `offset` starts; None at the end.
    def _next_line_start(self, offset: int) -> int | None:
        size = self._size()
        length = WINDOW_BYTES
        while True:
            window = self._read(offset, length)
            newline = window.find(b"\n")
            if newline >= 0:
                line_start = offset + newline + 1
                return line_start if line_start < size else None
            if offset + len(window) >= size:
                return None
            # a line longer than the window: read further
            length *= 2

    # Whole lines from a line start, a window at a time, to the end of the file.
    def _lines_from(self, offset: int) -> Iterator[ClusterIndexLine]:
        size = self._size()
        while offset < size:
            chunk = self._read_whole_lines(offset)
            line_start = offset
            for raw_line in chunk.splitlines(keepends=True):
                if raw_line.strip():
                    yield parse_cluster_index_line(raw_line.rstrip(b"\n"), line_start)
                line_start += len(raw_line)
            offset += len(chunk)

    # From a line start: as many whole lines as fit in a window, growing the
    # window when a single line is longer than it; the rest of the file at the end.
    def _read_whole_lines(self, offset: int) -> bytes:
        size = self._size()
        length = WINDOW_BYTES
        while True:
            window = self._read(offset, length)
            if offset + len(window) >= size:
                return window
            last_newline = window.rfind(b"\n")
            if last_newline >= 0:
                return window[: last_newline + 1]
            length *= 2

    def _read(self, offset: int, length: int) -> bytes:
        return self._http_client.get_byte_range(self._cluster_index_url, offset, offset + length - 1)

    def _size(self) -> int:
        if self._cluster_index_size is None:
            self._cluster_index_size = self._http_client.get_content_length(self._cluster_index_url)
        return self._cluster_index_size


# "<surt> <timestamp>\t<shard>\t<offset>\t<length>\t<block number>"
def parse_cluster_index_line(raw_line: bytes, start_offset: int) -> ClusterIndexLine:
    key, shard, offset, length, *_ = raw_line.decode("utf-8", errors="replace").split("\t")
    return ClusterIndexLine(
        start_offset=start_offset,
        surt=key.partition(" ")[0],
        block=IndexBlock(shard=shard, offset=int(offset), length=int(length)),
    )
