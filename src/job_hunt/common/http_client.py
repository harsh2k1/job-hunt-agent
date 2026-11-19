import threading
import time
from functools import cache
from typing import Any
from urllib.parse import urlparse

import httpx

from job_hunt.common.logger import get_logger
from job_hunt.common.settings import settings
from job_hunt.common.settings_schema import HttpSettings

logger = get_logger(__name__)

# worth another try: the server is busy or briefly broken
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
TOO_MANY_REQUESTS = 429
# the slowest a host is asked after telling us to slow down
MAX_HOST_GAP_SECONDS = 30.0
# zstd left out: httpx 0.28 fails on multi-frame zstd responses ("cannot
# use a decompressobj multiple times", seen from amazon.jobs)
ACCEPTED_ENCODINGS = "gzip, deflate, br"


class HttpRequestError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        # None when the request never got a response (timeout, DNS, ...)
        self.status_code = status_code


class HttpClient:
    """One shared HTTP client: retries, backoff and a per-host request gap.

    Every source goes through this, so politeness rules live in one place.
    """

    def __init__(self, http_settings: HttpSettings, transport: httpx.BaseTransport | None = None):
        self._settings = http_settings
        self._client = httpx.Client(
            timeout=http_settings.timeout_seconds,
            headers={"User-Agent": http_settings.user_agent, "Accept-Encoding": ACCEPTED_ENCODINGS},
            follow_redirects=True,
            transport=transport,
        )
        # when each host may next be asked; sources fetched in parallel share it
        self._next_request_time_by_host: dict[str, float] = {}
        # a host that answered "too many requests" is asked less often from then on
        self._gap_by_host: dict[str, float] = {}
        self._host_slot_lock = threading.Lock()

    def get_json(
        self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None
    ) -> Any:
        return self._send_with_retries("GET", url, params=params, headers=headers).json()

    def get_text(
        self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None
    ) -> str:
        return self._send_with_retries("GET", url, params=params, headers=headers).text

    # Bytes first to last, inclusive, of a file the server allows ranges on
    # (large published data files, read a piece at a time).
    def get_byte_range(self, url: str, first_byte: int, last_byte: int) -> bytes:
        headers = {"Range": f"bytes={first_byte}-{last_byte}"}
        return self._send_with_retries("GET", url, headers=headers).content

    def get_content_length(self, url: str) -> int:
        response = self._send_with_retries("HEAD", url)
        if "Content-Length" not in response.headers:
            raise HttpRequestError(f"HEAD {url} gave no Content-Length", status_code=response.status_code)
        return int(response.headers["Content-Length"])

    def post_json(
        self,
        url: str,
        json_body: Any,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return self._send_with_retries("POST", url, json=json_body, params=params, headers=headers).json()

    def close(self) -> None:
        self._client.close()

    def _send_with_retries(self, method: str, url: str, **request_kwargs: Any) -> httpx.Response:
        attempts = self._settings.max_retries + 1
        for attempt in range(attempts):
            is_last_attempt = attempt == attempts - 1
            self._wait_for_host_slot(url)
            try:
                response = self._client.request(method, url, **request_kwargs)
            except httpx.TransportError as error:
                if is_last_attempt:
                    raise HttpRequestError(f"{method} {url} failed: {error}") from error
                logger.warning("%s %s failed (%s), retrying", method, url, error)
                self._sleep_before_retry(attempt)
                continue

            if response.status_code == TOO_MANY_REQUESTS:
                self._slow_down_for(url)
            if response.status_code in RETRYABLE_STATUS_CODES and not is_last_attempt:
                logger.warning("%s %s returned %s, retrying", method, url, response.status_code)
                self._sleep_before_retry(attempt, response.headers.get("Retry-After"))
                continue
            if response.is_error:
                raise HttpRequestError(
                    f"{method} {url} returned {response.status_code}", status_code=response.status_code
                )
            return response

        raise AssertionError("unreachable: the last attempt always returns or raises")

    # Each request to a host takes the next free slot, min_gap after the one
    # before, under a lock so threads can't take the same slot; the wait for
    # it happens outside the lock, so other hosts aren't held up.
    def _wait_for_host_slot(self, url: str) -> None:
        host = urlparse(url).netloc
        with self._host_slot_lock:
            min_gap = self._gap_by_host.get(host, self._settings.min_seconds_between_requests_to_same_host)
            now = time.monotonic()
            slot = max(now, self._next_request_time_by_host.get(host, now))
            self._next_request_time_by_host[host] = slot + min_gap
        if slot > now:
            time.sleep(slot - now)

    # Workday sites, for one, answer 429 at a request a second: each 429
    # doubles that host's gap, up to MAX_HOST_GAP_SECONDS, for the client's life
    def _slow_down_for(self, url: str) -> None:
        host = urlparse(url).netloc
        with self._host_slot_lock:
            gap = self._gap_by_host.get(host, self._settings.min_seconds_between_requests_to_same_host)
            slower = min(MAX_HOST_GAP_SECONDS, max(gap * 2, 1.0))
            if slower > gap:
                logger.info("%s asked us to slow down: one request every %.0f s from now on", host, slower)
            self._gap_by_host[host] = slower

    def _sleep_before_retry(self, attempt: int, retry_after_header: str | None = None) -> None:
        # a server that says how long to wait knows better than our backoff
        if retry_after_header and retry_after_header.isdigit():
            time.sleep(int(retry_after_header))
            return
        time.sleep(self._settings.backoff_seconds * 2**attempt)


@cache
def get_http_client() -> HttpClient:
    return HttpClient(settings.http)
