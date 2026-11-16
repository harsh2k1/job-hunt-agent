"""The LLM's look at titles the free rules can't sort (title_rules.py): is
the job related to the candidate's fields at all? Titles only, many per
call, each title asked once and remembered across companies and runs.
Anything doubtful counts as related."""

import hashlib
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from job_hunt.common.logger import get_logger
from job_hunt.common.settings_schema import LlmScreeningSettings
from job_hunt.job_store.models import TitleScreening, utc_now
from job_hunt.job_store.title_screening_repository import TitleScreeningRepository
from job_hunt.llm.llm_client import LlmClient, LlmError, LlmUsageLimitError

logger = get_logger(__name__)

TITLE_PROMPT_TEMPLATE = (
    "For each job title, say whether the job could involve any of: {relevant_fields}. "
    "related = false only when the title plainly names a job with none of them (e.g. pharmacist, barista, "
    "sales associate, nurse, software engineer with nothing about data, ML or AI). Natural-science, "
    "medical and engineering-trade roles (biologist, chemist, geotechnical engineer) are unrelated unless "
    "the title names data, ML or AI; so are finance and banking roles (investment banking, trading, equity "
    "research, actuarial, audit, wealth management) unless the title names data, ML or AI. A title "
    "that names no field (e.g. 'Technology Graduate Programme', 'Summer Intern 2027', 'Technical "
    "Graduate', 'Applied Science Intern', 'Member of Technical Staff', 'General application', "
    "'Postdoctoral Fellow') is true: its advert decides. When in doubt, true. "
    "Judge the title only; never consider seniority or experience."
)


class TitleAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(description="the title's number exactly as given")
    related: bool


class TitleReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    titles: list[TitleAnswer]


def title_key(title: str) -> str:
    return " ".join(title.lower().split())


# the question asked, and its hash: stored answers only count for this one
def title_question(screening_settings: LlmScreeningSettings) -> tuple[str, str]:
    prompt = TITLE_PROMPT_TEMPLATE.format(relevant_fields=", ".join(screening_settings.relevant_fields))
    return prompt, hashlib.sha1(prompt.encode()).hexdigest()


@dataclass
class TitleScreeningReport:
    titles_asked: int = 0
    unrelated: int = 0
    failed_batches: int = 0
    stopped_by_usage_limit: bool = False


class TitleScreener:
    def __init__(
        self,
        screening_settings: LlmScreeningSettings,
        llm_client: LlmClient,
        repository: TitleScreeningRepository,
    ):
        self._settings = screening_settings
        self._llm_client = llm_client
        self._repository = repository
        self._prompt, self._question_hash = title_question(screening_settings)

    # asks about every title given that has no current answer; each batch is stored
    # as soon as it's answered, a failed batch's titles are asked next run
    def screen(self, titles: list[str]) -> TitleScreeningReport:
        report = TitleScreeningReport()
        known = self._repository.find((title_key(title) for title in titles), self._question_hash)
        new_titles = list(
            dict.fromkeys(title_key(title) for title in titles if title_key(title) not in known)
        )
        model = self._settings.first_pass_model
        for start in range(0, len(new_titles), self._settings.title_batch_size):
            batch = new_titles[start : start + self._settings.title_batch_size]
            numbered = "\n".join(f"{number}. {title}" for number, title in enumerate(batch))
            try:
                reply = self._llm_client.ask(f"Job titles:\n{numbered}", TitleReply, self._prompt, model)
            except LlmUsageLimitError as error:
                logger.warning("title screening stopped: %s", error)
                report.stopped_by_usage_limit = True
                break
            except LlmError as error:
                report.failed_batches += 1
                logger.warning("title screening batch failed: %s", error)
                continue
            answers = {answer.id: answer.related for answer in reply.titles if 0 <= answer.id < len(batch)}
            self._repository.save(
                TitleScreening(
                    title_key=batch[number], related=related, question_hash=self._question_hash, model=model,
                    screened_at=utc_now(),
                )
                for number, related in answers.items()
            )  # fmt: skip
            report.titles_asked += len(answers)
            report.unrelated += sum(1 for related in answers.values() if not related)
        return report
