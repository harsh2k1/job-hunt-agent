from abc import ABC, abstractmethod
from typing import ClassVar, TypeVar

from pydantic import BaseModel

from job_hunt.common.registry import Registry
from job_hunt.common.settings_schema import LlmSettings

ReplyT = TypeVar("ReplyT", bound=BaseModel)

llm_client_registry: Registry[type["LlmClient"]] = Registry("LLM client")


class LlmError(RuntimeError):
    """The model couldn't be asked, or its reply was unusable."""


class LlmUsageLimitError(LlmError):
    """The plan's usage limit is reached; every further call fails until it resets."""


class LlmClient(ABC):
    """One question, one structured answer. No conversation is kept: each
    call carries everything it needs, so nothing grows between calls."""

    name: ClassVar[str]

    @classmethod
    @abstractmethod
    def from_settings(cls, llm_settings: LlmSettings) -> "LlmClient": ...

    @abstractmethod
    def ask(self, prompt: str, reply_schema: type[ReplyT], system_prompt: str, model: str) -> ReplyT: ...


def register_llm_client(client_class: type[LlmClient]) -> type[LlmClient]:
    llm_client_registry.register(client_class.name)(client_class)
    return client_class
