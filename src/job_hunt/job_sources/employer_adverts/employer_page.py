"""Reading a job advert from an employer's own page, whatever site it's on.

Pages with schema.org JobPosting markup give the title, employer and advert
directly. Most careers sites have none, so the advert is the page's main
content as a reader view finds it (readability: the block of real prose,
without menus, banners and footers), titled by the page's heading."""

import re
from dataclasses import dataclass
from datetime import datetime

# neither ships type information
from lxml.etree import ParserError  # type: ignore[import-untyped]
from readability import Document  # type: ignore[import-untyped]
from readability.readability import Unparseable  # type: ignore[import-untyped]

from job_hunt.job_sources.html_to_text import html_to_text
from job_hunt.job_sources.job_posting_markup import (
    find_job_posting_markup,
    read_organisation_name,
    read_salary_text,
)
from job_hunt.job_sources.timestamps import parse_iso_timestamp

# tags left in the text: the markup's HTML was escaped twice (&lt;p&gt;)
LEFTOVER_TAG = re.compile(r"</?(?:p|div|span|br|li|ul|ol|b|strong|h[1-6])\b[^>]*>", re.IGNORECASE)


def _advert_text(markup_html: str) -> str:
    text = html_to_text(markup_html) or ""
    return (html_to_text(text) or "") if LEFTOVER_TAG.search(text) else text


@dataclass(frozen=True)
class EmployerPage:
    url: str
    title: str
    advert: str
    # who the markup says is hiring; None without markup
    organisation: str | None = None
    posted_at: datetime | None = None
    closes_at: datetime | None = None
    salary_text: str | None = None


def read_employer_page(url: str, page_html: str) -> EmployerPage | None:
    job_posting = find_job_posting_markup(page_html)
    if job_posting is not None and job_posting.get("title") and job_posting.get("description"):
        return EmployerPage(
            url=url,
            title=html_to_text(job_posting["title"]) or "",
            advert=_advert_text(job_posting["description"]),
            organisation=read_organisation_name(job_posting),
            posted_at=parse_iso_timestamp(job_posting.get("datePosted")),
            closes_at=parse_iso_timestamp(job_posting.get("validThrough")),
            salary_text=read_salary_text(job_posting),
        )
    try:
        document = Document(page_html)
        title, advert = document.short_title(), html_to_text(document.summary()) or ""
    # pages it can't make sense of (empty, framesets, broken markup)
    except (Unparseable, ParserError, ValueError):
        return None
    return EmployerPage(url=url, title=title, advert=advert) if title and advert else None
