import base64
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from job_hunt.common.logger import get_logger

logger = get_logger(__name__)

# read only: the agent never sends, labels or deletes anything
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class MailboxError(RuntimeError):
    pass


@dataclass(frozen=True)
class AlertEmail:
    message_id: str
    sender: str
    subject: str
    received_at: datetime
    html: str


class Mailbox(ABC):
    @abstractmethod
    def find_messages(self, query: str, max_messages: int) -> list[str]:
        """Ids of the newest messages matching a search, newest first."""

    @abstractmethod
    def read_message(self, message_id: str) -> AlertEmail: ...


class GmailMailbox(Mailbox):
    """Gmail through its API, with read-only access the owner grants once.

    The first run opens a Google consent page in the browser; the token it
    gives back is saved and refreshed from then on, so later runs need
    nobody. A personal inbox can't be read by a service account, which is
    why this is OAuth and not the sheet sync's key.
    """

    def __init__(self, client_secret_path: Path, token_path: Path, consent_timeout_seconds: int):
        self._client_secret_path = client_secret_path
        self._token_path = token_path
        self._consent_timeout_seconds = consent_timeout_seconds
        self._gmail: Any = None

    def find_messages(self, query: str, max_messages: int) -> list[str]:
        message_ids: list[str] = []
        page_token = None
        while len(message_ids) < max_messages:
            response = (
                self._service()
                .users()
                .messages()
                .list(
                    userId="me",
                    q=query,
                    maxResults=min(500, max_messages - len(message_ids)),
                    pageToken=page_token,
                )
                .execute()
            )
            message_ids.extend(message["id"] for message in response.get("messages", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return message_ids

    def read_message(self, message_id: str) -> AlertEmail:
        message = self._service().users().messages().get(userId="me", id=message_id, format="full").execute()
        return read_gmail_message(message)

    def _service(self) -> Any:
        if self._gmail is None:
            # imported here so the rest of the app doesn't load Google's client
            from googleapiclient.discovery import build

            self._gmail = build("gmail", "v1", credentials=self._credentials(), cache_discovery=False)
        return self._gmail

    # The saved token is refreshed on its own. When Google has expired it
    # (every 7 days while the OAuth app is in Testing), the consent page
    # opens again; if nobody approves in time the run fails with a clear
    # message instead of waiting forever.
    def _credentials(self) -> Any:
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        credentials = None
        if self._token_path.exists():
            credentials = Credentials.from_authorized_user_file(str(self._token_path), GMAIL_SCOPES)
        if credentials and credentials.valid:
            return credentials
        if credentials and credentials.refresh_token:
            try:
                credentials.refresh(Request())
            except RefreshError:
                logger.info("the saved Gmail token has expired; asking for consent again")
                credentials = None
        if credentials is None or not credentials.valid:
            credentials = self._ask_for_consent()
        self._save_token(credentials.to_json())
        return credentials

    def _ask_for_consent(self) -> Any:
        from google_auth_oauthlib.flow import InstalledAppFlow

        if not self._client_secret_path.exists():
            raise MailboxError(
                f"no Gmail OAuth client at {self._client_secret_path}; see README, Gmail setup"
            )
        logger.info("opening the browser for read-only Gmail consent")
        try:
            credentials = InstalledAppFlow.from_client_secrets_file(
                str(self._client_secret_path), GMAIL_SCOPES
            ).run_local_server(port=0, timeout_seconds=self._consent_timeout_seconds)
        # google-auth-oauthlib 1.0 has no clean timeout result: when no answer
        # comes back, it fails reading the missing redirect (None.replace)
        except AttributeError as error:
            raise MailboxError("Gmail consent wasn't given in time; run the fetch again to retry") from error
        return credentials

    # readable by the owner only: it opens the inbox
    def _save_token(self, token_json: str) -> None:
        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        self._token_path.write_text(token_json)
        os.chmod(self._token_path, 0o600)


def read_gmail_message(message: dict[str, Any]) -> AlertEmail:
    payload = message.get("payload", {})
    headers = {header["name"].lower(): header["value"] for header in payload.get("headers", [])}
    return AlertEmail(
        message_id=message["id"],
        sender=parseaddr(headers.get("from", ""))[1].lower(),
        subject=headers.get("subject", ""),
        received_at=datetime.fromtimestamp(int(message.get("internalDate", 0)) / 1000, tz=timezone.utc),
        html=_find_body(payload, "text/html") or _find_body(payload, "text/plain") or "",
    )


# Alert emails are multipart; the HTML part holds the links.
def _find_body(part: dict[str, Any], mime_type: str) -> str | None:
    if part.get("mimeType") == mime_type and part.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
    for child in part.get("parts", []):
        body = _find_body(child, mime_type)
        if body:
            return body
    return None
