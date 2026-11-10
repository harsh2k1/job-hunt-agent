from collections.abc import Iterator

from job_hunt.common.cache import Cache, get_cache
from job_hunt.common.logger import get_logger
from job_hunt.common.settings_schema import EmailAlertsSourceSettings, Settings
from job_hunt.job_sources.email_alerts.alert_parsers import parse_alert_email
from job_hunt.job_sources.email_alerts.job_links import AlertedJob
from job_hunt.job_sources.email_alerts.mailbox import GmailMailbox, Mailbox, MailboxError
from job_hunt.job_sources.job_source import (
    UNKNOWN_COMPANY_NAME,
    JobSource,
    JobSourceError,
    register_job_source,
)
from job_hunt.job_store.models import FetchedJob

logger = get_logger(__name__)


@register_job_source
class EmailAlertSource(JobSource[AlertedJob]):
    """Job alert emails, read from Gmail: the saved searches on LinkedIn and
    Indeed, and portals that can't be read directly (Handshake, Gradcracker,
    Welcome to the Jungle, ...). Each sender's layout has its own parser;
    jobs are keyed by site and id, so an advert in several alerts is one job.
    """

    name = "email_alerts"

    def __init__(self, source_settings: EmailAlertsSourceSettings, mailbox: Mailbox, cache: Cache):
        self._settings = source_settings
        self._mailbox = mailbox
        self._cache = cache

    @classmethod
    def from_settings(cls, settings: Settings) -> "EmailAlertSource":
        source_settings = settings.job_sources.email_alerts
        mailbox = GmailMailbox(
            source_settings.gmail_client_secret_path,
            source_settings.gmail_token_path,
            source_settings.gmail_consent_timeout_seconds,
        )
        return cls(source_settings, mailbox, get_cache())

    def _list_raw_postings(self) -> Iterator[AlertedJob]:
        try:
            message_ids = self._mailbox.find_messages(
                self._settings.gmail_query, self._settings.max_messages_per_run
            )
        except MailboxError as error:
            raise JobSourceError(f"{self.name}: {error}") from error
        logger.info("%s: %d alert emails", self.name, len(message_ids))
        for message_id in message_ids:
            # an email never changes, so each is read from Gmail once
            email = self._cache.get_or_compute(
                f"{self.name}:message:{message_id}",
                lambda message_id=message_id: self._mailbox.read_message(message_id),
                ttl_hours=self._settings.message_cache_ttl_hours,
            )
            yield from parse_alert_email(email)

    def _to_fetched_job(self, raw_posting: AlertedJob) -> FetchedJob:
        return FetchedJob(
            source=self.name,
            source_job_id=f"{raw_posting.site}:{raw_posting.job_id}",
            title=raw_posting.title,
            company_name=raw_posting.company or UNKNOWN_COMPANY_NAME,
            location=raw_posting.location,
            url=raw_posting.url,
            employer_url=raw_posting.url if raw_posting.is_ats_board else None,
            employment_type=raw_posting.employment_type,
            # when the alert arrived; the advert itself is a little older
            posted_at=raw_posting.received_at,
        )
