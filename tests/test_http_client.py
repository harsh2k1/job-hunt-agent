import httpx
import pytest

from common.http_client import HttpClient, HttpRequestError
from common.settings import settings


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


def test_requests_carry_our_user_agent():
    client, sent_requests = make_client_returning([200])
    client.get_text("https://example.com")
    assert sent_requests[0].headers["User-Agent"] == settings.http.user_agent
