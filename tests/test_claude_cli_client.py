import json
import subprocess

import pytest
from pydantic import BaseModel

from job_hunt.llm.claude_cli_client import ClaudeCliClient
from job_hunt.llm.llm_client import LlmError, LlmUsageLimitError


class Reply(BaseModel):
    answer: str


def finished(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=json.dumps(payload), stderr="")


SUCCESS = {"subtype": "success", "is_error": False, "structured_output": {"answer": "yes"}, "usage": {}}
NO_NETWORK = {
    "subtype": "success",
    "is_error": True,
    "result": "API Error: Can't reach the API server (ENOTFOUND)",
}
USAGE_LIMIT = {"subtype": "success", "is_error": True, "result": "You've hit your usage limit · resets 5pm"}
BAD_SCHEMA = {"subtype": "success", "is_error": True, "result": "Invalid JSON schema"}


@pytest.fixture
def run_returns(monkeypatch):
    calls: list[list[str]] = []

    def install(*payloads):
        replies = iter(payloads)
        monkeypatch.setattr("shutil.which", lambda binary: "/usr/bin/claude")

        def fake_run(command, **kwargs):
            calls.append(command)
            return finished(next(replies))

        monkeypatch.setattr(subprocess, "run", fake_run)
        return calls

    return install


def client() -> ClaudeCliClient:
    return ClaudeCliClient("claude", timeout_seconds=60, retries=2, retry_pause_seconds=0)


def test_a_structured_reply_is_validated(run_returns):
    calls = run_returns(SUCCESS)
    assert client().ask("q", Reply, "system", "haiku").answer == "yes"
    assert "--no-session-persistence" in calls[0] and "--json-schema" in calls[0]


def test_a_network_drop_is_tried_again(run_returns):
    calls = run_returns(NO_NETWORK, NO_NETWORK, SUCCESS)
    assert client().ask("q", Reply, "system", "haiku").answer == "yes"
    assert len(calls) == 3


def test_it_gives_up_after_the_retries(run_returns):
    run_returns(NO_NETWORK, NO_NETWORK, NO_NETWORK)
    with pytest.raises(LlmError, match="Can't reach"):
        client().ask("q", Reply, "system", "haiku")


def test_a_usage_limit_is_never_retried(run_returns):
    calls = run_returns(USAGE_LIMIT, SUCCESS)
    with pytest.raises(LlmUsageLimitError):
        client().ask("q", Reply, "system", "haiku")
    assert len(calls) == 1


def test_other_errors_fail_straight_away(run_returns):
    calls = run_returns(BAD_SCHEMA, SUCCESS)
    with pytest.raises(LlmError, match="Invalid JSON schema"):
        client().ask("q", Reply, "system", "haiku")
    assert len(calls) == 1
