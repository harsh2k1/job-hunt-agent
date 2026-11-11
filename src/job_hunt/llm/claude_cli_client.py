import json
import shutil
import subprocess
from typing import Any

from pydantic import ValidationError

from job_hunt.common.logger import get_logger
from job_hunt.common.settings_schema import LlmSettings
from job_hunt.llm.llm_client import LlmClient, LlmError, LlmUsageLimitError, ReplyT, register_llm_client

logger = get_logger(__name__)

# how the CLI words a spent usage limit
USAGE_LIMIT_MARKERS = ("usage limit", "session limit", "rate limit", "limit reached", "resets ")


@register_llm_client
class ClaudeCliClient(LlmClient):
    """Claude through the Claude Code CLI (`claude -p`), on the user's
    subscription: no API key. Every call is one-off (no saved session, no
    tools) and the reply is held to a JSON schema."""

    name = "claude_cli"

    def __init__(self, binary: str, timeout_seconds: int):
        self._binary = binary
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(cls, llm_settings: LlmSettings) -> "ClaudeCliClient":
        return cls(llm_settings.claude_cli_binary, llm_settings.timeout_seconds)

    def ask(self, prompt: str, reply_schema: type[ReplyT], system_prompt: str, model: str) -> ReplyT:
        payload = self._run(prompt, reply_schema, system_prompt, model)
        try:
            return reply_schema.model_validate(payload.get("structured_output"))
        except ValidationError as error:
            raise LlmError(f"the reply didn't match {reply_schema.__name__}: {error}") from error

    def _run(self, prompt: str, reply_schema: type[ReplyT], system_prompt: str, model: str) -> dict[str, Any]:
        binary = shutil.which(self._binary)
        if binary is None:
            raise LlmError(f"'{self._binary}' isn't on PATH; install Claude Code and log in")
        command = [
            binary, "-p", "--output-format", "json", "--model", model, "--no-session-persistence",
            "--tools", "", "--append-system-prompt", system_prompt,
            "--json-schema", json.dumps(reply_schema.model_json_schema()),
        ]  # fmt: skip
        try:
            finished = subprocess.run(
                command,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise LlmError(f"claude took longer than {self._timeout_seconds}s") from error
        try:
            payload = json.loads(finished.stdout)
        except json.JSONDecodeError as error:
            raise _error(
                f"claude exited {finished.returncode}: {(finished.stderr or finished.stdout)[-300:]}"
            ) from error
        if payload.get("is_error") or payload.get("subtype") != "success":
            raise _error(f"claude reported: {payload.get('result') or payload.get('subtype')}")
        usage = payload.get("usage") or {}
        logger.info(
            "claude %s: %.0fs, %d in (+%d cached) / %d out tokens, $%.4f",
            model,
            payload.get("duration_ms", 0) / 1000,
            usage.get("input_tokens", 0),
            usage.get("cache_read_input_tokens", 0),
            usage.get("output_tokens", 0),
            payload.get("total_cost_usd", 0.0),
        )
        return payload


def _error(message: str) -> LlmError:
    lowered = message.lower()
    return (
        LlmUsageLimitError(message)
        if any(marker in lowered for marker in USAGE_LIMIT_MARKERS)
        else LlmError(message)
    )
