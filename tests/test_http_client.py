import httpx
import pytest

from job_hunt.common.http_client import HttpClient, HttpRequestError
from job_hunt.common.settings import settings


def make_client_returning(status_codes: list[int]) -> tuple[HttpClient, list[httpx.Request]]:
    """A client whose server answers with these status codes, one per request."""
    sent_requests: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent_requests.append(request)
        return httpx.Response(status_codes[len(sent_requests) - 1], json={"ok": True})

    return HttpClient(settings.http, transport=httpx.MockTransport(answer)), sent_requests


def test_server_error_is_retried_until_it_succeeds():
    client, sent_requests = make_client_returning([503, 200])
    assert client.get_json("https://example.com/jobs") == {"ok": True}
    assert len(sent_requests) == 2


def test_not_found_is_not_retried_and_keeps_the_status_code():
    client, sent_requests = make_client_returning([404])
    with pytest.raises(HttpRequestError) as error:
        client.get_json("https://boards-api.greenhouse.io/v1/boards/nobody/jobs")
    assert error.value.status_code == 404
    assert len(sent_requests) == 1


def test_gives_up_after_max_retries():
    attempts = settings.http.max_retries + 1
    client, sent_requests = make_client_returning([503] * attempts)
    with pytest.raises(HttpRequestError) as error:
        client.get_json("https://example.com/jobs")
    assert error.value.status_code == 503
    assert len(sent_requests) == attempts


def test_responses_are_never_asked_for_in_zstd():
    client, sent_requests = make_client_returning([200])
    client.get_text("https://example.com/")
    assert "zstd" not in sent_requests[0].headers["Accept-Encoding"]


def test_requests_carry_our_user_agent():
    client, sent_requests = make_client_returning([200])
    client.get_text("https://example.com")
    assert sent_requests[0].headers["User-Agent"] == settings.http.user_agent


def test_byte_range_and_size_of_a_published_file():
    data = b"0123456789"

    def answer(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": str(len(data))})
        first, last = map(int, request.headers["Range"].removeprefix("bytes=").split("-"))
        return httpx.Response(206, content=data[first : last + 1])

    client = HttpClient(settings.http, transport=httpx.MockTransport(answer))
    assert client.get_content_length("https://data.example/file") == 10
    assert client.get_byte_range("https://data.example/file", 2, 4) == b"234"


def test_requests_to_one_host_from_several_threads_stay_spaced_out():
    import threading
    import time

    sent_at: list[float] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent_at.append(time.monotonic())
        return httpx.Response(200, text="ok")

    gap = 0.1
    spaced = settings.http.model_copy(update={"min_seconds_between_requests_to_same_host": gap})
    client = HttpClient(spaced, transport=httpx.MockTransport(answer))
    threads = [
        threading.Thread(target=client.get_text, args=("https://boards.example.com/a",)) for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    gaps = [later - earlier for earlier, later in zip(sorted(sent_at), sorted(sent_at)[1:], strict=False)]
    assert len(sent_at) == 4 and all(spacing >= gap * 0.95 for spacing in gaps)


def test_a_host_that_says_too_many_requests_is_asked_less_often_from_then_on():
    answers = iter([429, 200, 200])

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(answers), text="ok")

    client = HttpClient(settings.http, transport=httpx.MockTransport(answer))
    client.get_text("https://acme.wd3.myworkdayjobs.com/a")
    # its gap went from the setting (0 in tests) to a second, for every later request
    assert client._gap_by_host["acme.wd3.myworkdayjobs.com"] == 1.0
    assert "boards.example.com" not in client._gap_by_host
