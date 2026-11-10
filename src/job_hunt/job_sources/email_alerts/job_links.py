import base64
import binascii
import gzip
import html
import re
import zlib
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from job_hunt.job_sources.ats_boards.ats_board_links import find_ats_board, strip_tracking_params

ANCHOR_PATTERN = re.compile(r"<a\b[^>]*\bhref=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
TAG_PATTERN = re.compile(r"<[^>]+>")
# Indeed wraps every link as cts.indeed.com/v3/<base64 of gzip>; the real
# address is inside, so it can be read without following (clicking) it
INDEED_TRACKER_PATTERN = re.compile(r"https?://cts\.indeed\.com/v3/([A-Za-z0-9_-]+)")
URL_IN_TEXT_PATTERN = re.compile(r"https?://[^\s\"'\\<>]+")

# link text that says nothing about the job
GENERIC_LINK_TEXTS = frozenset(
    {"", "view job", "view", "apply", "apply now", "see job", "see more", "view details", "learn more"}
)
UNTITLED_ADVERT = "Untitled advert (from an alert email)"


@dataclass(frozen=True)
class AlertedJob:
    site: str
    # the site's own id for the advert, so the same advert in several alerts is one job
    job_id: str
    url: str
    title: str
    received_at: datetime
    company: str | None = None
    location: str | None = None
    employment_type: str | None = None
    # set when the link goes straight to an advert on a company's ATS board
    is_ats_board: bool = False


@dataclass(frozen=True)
class JobLinkPattern:
    site: str
    pattern: re.Pattern[str]
    canonical_url: str


# Only formats confirmed in real alert emails (Nov 2026).
JOB_LINK_PATTERNS = (
    JobLinkPattern(
        "linkedin",
        re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?#]*-)?(\d+)", re.IGNORECASE),
        "https://www.linkedin.com/jobs/view/{job_id}",
    ),
    # viewjob?jk=<key>, or an ad link whose jrtk tracking value ends in the key
    JobLinkPattern(
        "indeed",
        re.compile(
            r"indeed\.[a-z.]+/[^\s\"']*?(?:[?&](?:jk|vjk)=|[?&]jrtk=[^&\s\"']*-)([0-9a-f]{16})(?![0-9a-f])",
            re.IGNORECASE,
        ),
        "https://uk.indeed.com/viewjob?jk={job_id}",
    ),
)


@dataclass(frozen=True)
class JobLink:
    site: str
    job_id: str
    url: str
    is_ats_board: bool = False


def read_job_link(href: str) -> JobLink | None:
    href = unwrap_tracking_link(html.unescape(href).strip())
    for link_pattern in JOB_LINK_PATTERNS:
        match = link_pattern.pattern.search(href)
        if match:
            job_id = match.group(1).lower()
            return JobLink(link_pattern.site, job_id, link_pattern.canonical_url.format(job_id=job_id))
    board = find_ats_board(href)
    # one advert, not the board's front page: every supported ATS puts an
    # advert at least two path segments deep (/perplexity/1234)
    if board is not None and len([part for part in urlsplit(href).path.split("/") if part]) >= 2:
        url = strip_tracking_params(href)
        return JobLink(board.ats_platform.value, url, url, is_ats_board=True)
    return None


# Decoded locally: following a tracker would count as a click, and some
# links in alerts are actions ("This is a bad match").
def unwrap_tracking_link(href: str) -> str:
    match = INDEED_TRACKER_PATTERN.match(href)
    if match is None:
        return href
    payload = match.group(1)
    try:
        decoded = gzip.decompress(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (binascii.Error, OSError, EOFError, zlib.error):
        return href
    inner_url = URL_IN_TEXT_PATTERN.search(decoded.decode("utf-8", errors="replace"))
    return inner_url.group(0) if inner_url else href


def link_text(raw_html: str) -> str:
    return " ".join(html.unescape(TAG_PATTERN.sub(" ", raw_html)).replace("​", " ").split())


# Any sender: every link to a known job site, titled by its best link text.
def find_job_links(email_html: str, received_at: datetime) -> list[AlertedJob]:
    jobs: dict[tuple[str, str], AlertedJob] = {}
    for href, raw_text in ANCHOR_PATTERN.findall(email_html):
        link = read_job_link(href)
        if link is None:
            continue
        text = link_text(raw_text)
        known = jobs.get((link.site, link.job_id))
        # an alert links each job several times (title, logo, "View job");
        # keep the text that best names it
        if known is not None and (known.title != UNTITLED_ADVERT or text.lower() in GENERIC_LINK_TEXTS):
            continue
        title = text if text.lower() not in GENERIC_LINK_TEXTS else UNTITLED_ADVERT
        jobs[(link.site, link.job_id)] = AlertedJob(
            link.site, link.job_id, link.url, title, received_at, is_ats_board=link.is_ats_board
        )
    return list(jobs.values())
