from job_hunt.common.settings_schema import LlmSettings

# imported so every client registers itself before the factory is used
import job_hunt.llm.claude_cli_client  # noqa: F401  # isort: skip
from job_hunt.llm.llm_client import LlmClient, llm_client_registry


class LlmClientFactory:
    @staticmethod
    def create(llm_settings: LlmSettings) -> LlmClient:
        return llm_client_registry.get(llm_settings.backend).from_settings(llm_settings)
