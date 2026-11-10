"""One parser per alert sender, for the parts a link alone doesn't give
(company, place). Layouts read from real alert emails, Nov 2026. A sender
with no parser still gets its job links read."""

import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import replace
from typing import ClassVar

from job_hunt.job_sources.email_alerts.job_links import (
    ANCHOR_PATTERN,
    TAG_PATTERN,
    AlertedJob,
    find_job_links,
    link_text,
    read_job_link,
)
from job_hunt.job_sources.email_alerts.mailbox import AlertEmail

SPAN_PATTERN = re.compile(r"<span\b[^>]*>(.*?)</span>", re.IGNORECASE | re.DOTALL)


class AlertParser(ABC):
    senders: ClassVar[frozenset[str]]

    @abstractmethod
    def parse(self, email: AlertEmail) -> list[AlertedJob]: ...


class LinkedInAlertParser(AlertParser):
    """Each job: a title link, with "Company · Place" either inside the link
    (jobs-noreply) or in the text after it (jobalerts-noreply)."""

    senders = frozenset({"jobalerts-noreply@linkedin.com", "jobs-noreply@linkedin.com"})

    def parse(self, email: AlertEmail) -> list[AlertedJob]:
        jobs: dict[str, AlertedJob] = {}
        title_links = [
            (match, link)
            for match in ANCHOR_PATTERN.finditer(email.html)
            if link_text(match.group(2))
            and (link := read_job_link(match.group(1)))
            and link.site == "linkedin"
        ]
        for position, (match, link) in enumerate(title_links):
            if link.job_id in jobs:
                continue
            card_end = (
                title_links[position + 1][0].start() if position + 1 < len(title_links) else len(email.html)
            )
            title, *rest_of_link = text_parts(match.group(2))
            company, location = read_company_and_place(rest_of_link)
            if company is None:
                company, location = read_company_and_place(text_parts(email.html[match.end() : card_end]))
            jobs[link.job_id] = AlertedJob(
                link.site, link.job_id, link.url, title, email.received_at, company, location
            )
        return list(jobs.values())


def text_parts(fragment_html: str) -> list[str]:
    return [text for text in (link_text(part) for part in TAG_PATTERN.split(fragment_html)) if text]


# the first "Company · Place" line
def read_company_and_place(texts: list[str]) -> tuple[str | None, str | None]:
    for text in texts:
        if " · " in text:
            company, _, place = text.partition(" · ")
            return company.strip() or None, place.strip() or None
    return None, None


class IndeedMatchParser(AlertParser):
    """Indeed's "matched to your profile" emails. The subject names a job:
    "Title @ Company" when the email has one job, "Title at Company in
    Place and 3 more new jobs" for a digest. The rest of a digest's jobs
    are titled by their links."""

    senders = frozenset({"donotreply@match.indeed.com"})

    def parse(self, email: AlertEmail) -> list[AlertedJob]:
        jobs = [job for job in find_job_links(email.html, email.received_at) if job.site == "indeed"]
        named = read_indeed_subject(email.subject)
        if named is None:
            return jobs
        title, company, location = named
        # the subject's job is the one titled the same, or the only one
        for position, job in enumerate(jobs):
            if job.title == title or len(jobs) == 1:
                jobs[position] = replace(job, title=title, company=company, location=location)
                break
        return jobs


SINGLE_JOB_SUBJECT = re.compile(r"^(?P<title>.+) @ (?P<company>.+)$")
DIGEST_SUBJECT = re.compile(r"^(?P<title>.+?) at (?P<company>.+?) in (?P<place>.+?) and \d+ more new jobs?$")


def read_indeed_subject(subject: str) -> tuple[str, str, str | None] | None:
    digest = DIGEST_SUBJECT.match(subject)
    if digest:
        return digest["title"].strip(), digest["company"].strip(), digest["place"].strip()
    single = SINGLE_JOB_SUBJECT.match(subject)
    if single:
        return single["title"].strip(), single["company"].strip(), None
    return None


class HandshakeAlertParser(AlertParser):
    """Each job is one link holding three spans: company, title, and
    "Type • Place". Links go through Handshake's click tracker and can't be
    read offline, so the job is keyed by company and title and its link is
    the tracker, which still works when clicked."""

    senders = frozenset({"handshake@notifications.joinhandshake.com"})

    def parse(self, email: AlertEmail) -> list[AlertedJob]:
        jobs: dict[str, AlertedJob] = {}
        for href, inner_html in ANCHOR_PATTERN.findall(email.html):
            spans = [link_text(span) for span in SPAN_PATTERN.findall(inner_html)]
            spans = [span for span in spans if span]
            if len(spans) < 2:
                continue
            company, title = spans[0], spans[1]
            employment_type, _, location = spans[2].partition("•") if len(spans) > 2 else ("", "", "")
            job_id = hashlib.sha1(f"{company}|{title}".encode()).hexdigest()[:16]
            jobs.setdefault(
                job_id,
                AlertedJob(
                    "handshake",
                    job_id,
                    href.replace("&amp;", "&"),
                    title,
                    email.received_at,
                    company,
                    location.strip() or None,
                    employment_type.strip() or None,
                ),
            )
        return list(jobs.values())


ALERT_PARSERS: tuple[AlertParser, ...] = (LinkedInAlertParser(), IndeedMatchParser(), HandshakeAlertParser())


def parse_alert_email(email: AlertEmail) -> list[AlertedJob]:
    for parser in ALERT_PARSERS:
        if email.sender in parser.senders:
            return parser.parse(email)
    return find_job_links(email.html, email.received_at)
