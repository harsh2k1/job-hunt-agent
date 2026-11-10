"""Alert emails shaped like the real ones (Nov 2026), anonymised: same
layout and link formats, no personal details or real tracking tokens."""

import base64
import gzip
from datetime import datetime, timezone

import googleapiclient.discovery
import pytest
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from job_hunt.common.settings import settings
from job_hunt.job_sources.email_alerts.alert_parsers import parse_alert_email, read_indeed_subject
from job_hunt.job_sources.email_alerts.email_alert_source import EmailAlertSource
from job_hunt.job_sources.email_alerts.job_links import UNTITLED_ADVERT, find_job_links, unwrap_tracking_link
from job_hunt.job_sources.email_alerts.mailbox import AlertEmail, GmailMailbox, Mailbox, read_gmail_message

RECEIVED = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)


def indeed_tracker(inner_url: str) -> str:
    payload = base64.urlsafe_b64encode(gzip.compress(f'{{"u":"{inner_url}"}}'.encode())).decode().rstrip("=")
    return f"https://cts.indeed.com/v3/{payload}"


def email(sender: str, subject: str, body: str) -> AlertEmail:
    return AlertEmail(message_id="m1", sender=sender, subject=subject, received_at=RECEIVED, html=body)


LINKEDIN_JOB = "https://www.linkedin.com/comm/jobs/view/{job_id}/?trackingId=abc&amp;refId=x"
# jobalerts-noreply: title link, then "Company · Place" after it
LINKEDIN_ALERT = f"""
<table>
 <tr><td><a href="{LINKEDIN_JOB.format(job_id=4485053470)}"><img src="logo.png"></a></td>
     <td><a href="{LINKEDIN_JOB.format(job_id=4485053470)}">
           <table><tr><td>Data Scientist, Product Analytics</td></tr></table></a>
         <p>Meta · London</p><p>Actively recruiting</p></td></tr>
 <tr><td><a href="{LINKEDIN_JOB.format(job_id=4486840619)}">Junior Data Scientist</a>
         <p>EDITED · London (Hybrid)</p><p>1 school alum</p></td></tr>
 <tr><td><a href="https://www.linkedin.com/comm/jobs/search-results/?keywords=Data">See all jobs</a></td></tr>
 <tr><td><a href="https://www.linkedin.com/comm/jobs/alerts">Manage alerts</a></td></tr>
</table>"""
# jobs-noreply: title, "Company · Place" and badges all inside the link
LINKEDIN_HIRING = f"""
<a href="{LINKEDIN_JOB.format(job_id=4477785690)}"><span>Data Analyst - Fully Remote</span>
   <span>FindCore · United Kingdom (Remote)</span><span>Easy Apply</span></a>"""


def test_linkedin_alert_gives_title_company_and_place():
    jobs = parse_alert_email(email("jobalerts-noreply@linkedin.com", "Meta is hiring", LINKEDIN_ALERT))
    assert [(job.job_id, job.title, job.company, job.location) for job in jobs] == [
        ("4485053470", "Data Scientist, Product Analytics", "Meta", "London"),
        ("4486840619", "Junior Data Scientist", "EDITED", "London (Hybrid)"),
    ]
    assert jobs[0].url == "https://www.linkedin.com/jobs/view/4485053470"


def test_linkedin_layout_with_everything_inside_the_link():
    [job] = parse_alert_email(email("jobs-noreply@linkedin.com", "FindCore is hiring", LINKEDIN_HIRING))
    assert (job.title, job.company, job.location) == (
        "Data Analyst - Fully Remote",
        "FindCore",
        "United Kingdom (Remote)",
    )


INDEED_JOB_AD = "https://uk.indeed.com/pagead/clk?from=jobmatch&jrtk=5-cmh1-1-1k6j9a-{job_key}&ad=xyz"


def indeed_match(*job_keys_and_titles: tuple[str, str]) -> str:
    cards = "".join(
        f'<a href="{indeed_tracker(INDEED_JOB_AD.format(job_key=key))}">{title}</a>'
        f'<a href="{indeed_tracker(INDEED_JOB_AD.format(job_key=key))}">​ View job ​</a>'
        for key, title in job_keys_and_titles
    )
    feedback = indeed_tracker("https://match.indeed.com/invitations/not-interested?tk=abc")
    return f'{cards}<a href="{feedback}">​ This is a bad match ​</a>'


def test_indeed_tracker_links_are_decoded_without_following_them():
    inner = INDEED_JOB_AD.format(job_key="6f9682cd7254fed0")
    assert unwrap_tracking_link(indeed_tracker(inner)) == inner
    # anything that isn't a readable tracker is left as it is
    assert (
        unwrap_tracking_link("https://cts.indeed.com/v3/not-base64!")
        == "https://cts.indeed.com/v3/not-base64!"
    )


def test_indeed_single_match_takes_title_and_company_from_the_subject():
    body = indeed_match(("6f9682cd7254fed0", "Stock Controller"))
    [job] = parse_alert_email(email("donotreply@match.indeed.com", "Stock Controller @ Delikatesy Ltd", body))
    assert (job.job_id, job.title, job.company) == ("6f9682cd7254fed0", "Stock Controller", "Delikatesy Ltd")
    assert job.url == "https://uk.indeed.com/viewjob?jk=6f9682cd7254fed0"


def test_indeed_digest_names_its_first_job_and_keeps_the_rest():
    body = indeed_match(("984717a61f2b3c99", "Barista"), ("b8156497fc32a83c", "Cake Decorator"))
    subject = "Barista at Goldex Ltd in London and 1 more new job"
    jobs = parse_alert_email(email("donotreply@match.indeed.com", subject, body))
    assert [(job.title, job.company, job.location) for job in jobs] == [
        ("Barista", "Goldex Ltd", "London"),
        ("Cake Decorator", None, None),
    ]


@pytest.mark.parametrize(
    "subject, expected",
    [
        ("Pizza Chef @ Carmy's", ("Pizza Chef", "Carmy's", None)),
        (
            "Supervisor @ The Queen's Head at Mother Kellys in London and 4 more new jobs",
            (
                "Supervisor @ The Queen's Head",
                "Mother Kellys",
                "London",
            ),
        ),
        ("Your weekly summary", None),
    ],
)
def test_indeed_subjects(subject, expected):
    assert read_indeed_subject(subject) == expected


HANDSHAKE_ALERT = """
<a href="https://email.notifications.joinhandshake.com/c/eJyEks1u?a=1&amp;b=2"><img src="logo.png"></a>
<a href="https://email.notifications.joinhandshake.com/c/eJyEks1u?a=1&amp;b=2">
  <span>TikTok Inc.</span><span>UK Business Planning Strategy Intern - 2027 Start</span>
  <span> Internship • London, England (Onsite) </span></a>
<a href="https://email.notifications.joinhandshake.com/c/eJyEkb1u">Update your interests</a>"""


def test_handshake_job_comes_from_the_three_spans():
    [job] = parse_alert_email(email("handshake@notifications.joinhandshake.com", "New jobs", HANDSHAKE_ALERT))
    assert (job.company, job.title, job.location, job.employment_type) == (
        "TikTok Inc.",
        "UK Business Planning Strategy Intern - 2027 Start",
        "London, England (Onsite)",
        "Internship",
    )
    # keyed by company and title, since the tracker link can't be read offline
    assert len(job.job_id) == 16
    assert job.url == "https://email.notifications.joinhandshake.com/c/eJyEks1u?a=1&b=2"


GENERIC_ALERT = """
<a href="https://jobs.ashbyhq.com/perplexity/d5bc2302?utm_source=alert">Research Engineer</a>
<a href="https://jobs.ashbyhq.com/perplexity">See all Perplexity jobs</a>
<a href="https://uk.indeed.com/viewjob?jk=c97f46cd24e3190a&amp;from=alert">View job</a>"""


def test_unknown_sender_still_gets_its_job_links_read():
    jobs = {
        job.site: job for job in parse_alert_email(email("alerts@somewhere.example", "Jobs", GENERIC_ALERT))
    }
    # the board's front page isn't an advert
    assert set(jobs) == {"ashby", "indeed"}
    assert jobs["ashby"].is_ats_board
    assert jobs["ashby"].url == "https://jobs.ashbyhq.com/perplexity/d5bc2302"
    assert jobs["indeed"].title == UNTITLED_ADVERT


def test_best_link_text_wins_whatever_the_order():
    body = '<a href="https://uk.indeed.com/viewjob?jk=c97f46cd24e3190a">View job</a>' + (
        '<a href="https://uk.indeed.com/viewjob?jk=c97f46cd24e3190a">Data Scientist</a>'
    )
    [job] = find_job_links(body, RECEIVED)
    assert job.title == "Data Scientist"


def gmail_message(
    message_id: str, html: str, sender: str = "LinkedIn <jobalerts-noreply@linkedin.com>"
) -> dict:
    return {
        "id": message_id,
        "internalDate": "1794657600000",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [{"name": "From", "value": sender}, {"name": "Subject", "value": "Meta is hiring"}],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(b"plain").decode()}},
                {"mimeType": "text/html", "body": {"data": base64.urlsafe_b64encode(html.encode()).decode()}},
            ],
        },
    }


def test_gmail_message_is_read_into_an_alert_email():
    alert = read_gmail_message(gmail_message("m1", LINKEDIN_ALERT))
    assert alert.sender == "jobalerts-noreply@linkedin.com"
    assert alert.received_at == datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
    assert "4485053470" in alert.html


class FakeMailbox(Mailbox):
    def __init__(self, messages: dict[str, str]):
        self.queries: list[str] = []
        self.reads: list[str] = []
        self._messages = messages

    def find_messages(self, query: str, max_messages: int) -> list[str]:
        self.queries.append(query)
        return list(self._messages)[:max_messages]

    def read_message(self, message_id: str) -> AlertEmail:
        self.reads.append(message_id)
        return read_gmail_message(gmail_message(message_id, self._messages[message_id]))


def test_alerts_become_jobs_with_company_and_place(cache):
    mailbox = FakeMailbox({"m1": LINKEDIN_ALERT, "m2": LINKEDIN_ALERT})
    jobs = EmailAlertSource(settings.job_sources.email_alerts, mailbox, cache).fetch_jobs()

    assert {job.source_job_id for job in jobs} == {"linkedin:4485053470", "linkedin:4486840619"}
    meta = next(job for job in jobs if job.source_job_id == "linkedin:4485053470")
    assert (meta.company_name, meta.location) == ("Meta", "London")
    assert mailbox.queries == [settings.job_sources.email_alerts.gmail_query]


def test_each_email_is_read_from_gmail_once(cache):
    mailbox = FakeMailbox({"m1": LINKEDIN_ALERT})
    EmailAlertSource(settings.job_sources.email_alerts, mailbox, cache).fetch_jobs()
    EmailAlertSource(settings.job_sources.email_alerts, mailbox, cache).fetch_jobs()
    assert mailbox.reads == ["m1"]


def test_query_never_reaches_personal_mail():
    query = settings.job_sources.email_alerts.gmail_query
    for personal_sender in (
        "messages-noreply@linkedin.com",
        "invitations@linkedin.com",
        "no-reply@indeed.com",
    ):
        assert personal_sender not in query
    assert "linkedin.com OR" not in query.replace("@linkedin.com", "")


class FakeGmailService:
    """Just enough of googleapiclient's Gmail service for a message search."""

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, **query):
        return self

    def execute(self):
        return {"messages": [{"id": "m1"}]}


class FakeConsentFlow:
    def __init__(self, credentials):
        self.credentials = credentials
        self.timeouts: list[int] = []

    def run_local_server(self, port, timeout_seconds):
        self.timeouts.append(timeout_seconds)
        return self.credentials


def test_expired_token_asks_for_consent_again_and_saves_the_new_one(tmp_path, monkeypatch):
    token_path = tmp_path / "token.json"
    token_path.write_text(
        '{"token": "old", "refresh_token": "r", "client_id": "c", "client_secret": "s", '
        '"token_uri": "https://oauth2.googleapis.com/token", "expiry": "2026-11-01T00:00:00Z"}'
    )
    client_secret_path = tmp_path / "client.json"
    client_secret_path.write_text("{}")

    def refused_refresh(self, request):
        raise RefreshError("invalid_grant: Token has been expired or revoked.")

    consent = FakeConsentFlow(Credentials(token="new", refresh_token="r2", client_id="c", client_secret="s"))
    monkeypatch.setattr(Credentials, "refresh", refused_refresh)
    monkeypatch.setattr(InstalledAppFlow, "from_client_secrets_file", lambda *args: consent)
    monkeypatch.setattr(googleapiclient.discovery, "build", lambda *args, **kwargs: FakeGmailService())

    mailbox = GmailMailbox(client_secret_path, token_path, consent_timeout_seconds=300)
    assert mailbox.find_messages("newer_than:7d", 10) == ["m1"]
    assert consent.timeouts == [300]
    assert '"token": "new"' in token_path.read_text()
