import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

from playwright.sync_api import BrowserContext, Page
from playwright.sync_api import Error as PlaywrightError

from job_hunt.common.logger import get_logger
from job_hunt.common.settings_schema import KclTargetConnectSourceSettings, PortalLogin, Settings
from job_hunt.job_sources.job_source import JobSource, JobSourceError, register_job_source
from job_hunt.job_sources.location_matching import TargetLocationMatcher
from job_hunt.job_sources.portals.portal_browser import open_portal_browser
from job_hunt.job_store.models import FetchedJob, utc_now

logger = get_logger(__name__)

SITE_URL = "https://kcl.targetconnect.net"
HOME_URL = f"{SITE_URL}/student/home.html"
JOBS_URL = f"{SITE_URL}/student/jobs.html"
ADVERT_URL = f"{SITE_URL}/student/jobs.html?id={{job_id}}"
MICROSOFT_SIGN_IN = "https://login.microsoftonline.com/"
# the student button, never "Operator sign in" (the staff login)
STUDENT_SIGN_IN = re.compile(r"^\s*sign in\s*$", re.IGNORECASE)
RESULTS_PER_PAGE = 100
# one rendered result card; everything is read from what the page shows
READ_CARDS_SCRIPT = """els => els.map(icon => {
    const card = icon.closest('.card-body');
    const text = selector => (card.querySelector(selector)?.innerText || '').trim();
    return {id: icon.dataset.id, title: text('h2'), organisation: text('.card-job-org'),
            spec: text('.card-job-spec'), days: text('.card-job-days')};
})"""
CARD_SELECTOR = "i[data-id][data-action='save']"
DAYS_TO_APPLY = re.compile(r"(\d+)\s+days?\s+to\s+apply", re.IGNORECASE)


@dataclass(frozen=True)
class TargetConnectCard:
    job_id: str
    title: str
    organisation: str
    # "Graduate Employment | UK - London | £45,000 - £49,999"
    spec: str
    # "52 days to apply", "Last day to apply"
    days: str


class SignInNeeded(Exception):
    """The saved session is gone and a person has to sign in."""


@register_job_source
class KclTargetConnectSource(JobSource[TargetConnectCard]):
    """King's CareerConnect (Target Connect): jobs posted to King's students.

    Read in a real browser with a saved profile: King's sign-in uses a
    passkey or 2FA, so a person signs in once and later runs reuse the
    session (and King's single sign-on renews it quietly). If that ever
    stops working, a visible window opens with the username filled in and
    waits for the person to finish.
    """

    name = "kcl_target_connect"
    listing_rank = 40

    def __init__(
        self,
        source_settings: KclTargetConnectSourceSettings,
        login: PortalLogin | None,
        location_matcher: TargetLocationMatcher,
    ):
        self._settings = source_settings
        self._login = login
        self._location_matcher = location_matcher

    @classmethod
    def from_settings(cls, settings: Settings) -> "KclTargetConnectSource":
        return cls(
            settings.job_sources.kcl_target_connect,
            settings.portal_logins.kcl,
            TargetLocationMatcher(settings.job_search.target_location_keywords),
        )

    def _list_raw_postings(self) -> Iterator[TargetConnectCard]:
        cards = self._read_with_browser(headless=True)
        if cards is None:
            if not self._settings.allow_visible_sign_in:
                raise JobSourceError(f"{self.name}: the King's session has ended and visible sign-in is off")
            logger.info("%s: the King's session has ended; opening a window to sign in", self.name)
            cards = self._read_with_browser(headless=False)
        if cards is None:
            raise JobSourceError(f"{self.name}: nobody signed in to King's within the time allowed")
        yield from cards

    # None when a sign-in is needed and couldn't be done in this browser
    def _read_with_browser(self, headless: bool) -> list[TargetConnectCard] | None:
        try:
            with open_portal_browser(self._settings.browser_profile_dir, headless) as browser:
                page = self._signed_in_page(browser, wait_for_person=not headless)
                return self._read_all_cards(page)
        except SignInNeeded:
            return None
        except PlaywrightError as error:
            raise JobSourceError(f"{self.name}: the browser failed: {error}") from error

    def _signed_in_page(self, browser: BrowserContext, wait_for_person: bool) -> Page:
        page = browser.pages[0] if browser.pages else browser.new_page()
        page.goto(HOME_URL, wait_until="domcontentloaded")
        if is_signed_in(page.url):
            return page
        page.goto(f"{SITE_URL}/unauth/student/login", wait_until="domcontentloaded")
        page.locator("a, button").filter(has_text=STUDENT_SIGN_IN).first.click()
        wait_seconds = (
            self._settings.sign_in_wait_seconds if wait_for_person else self._settings.silent_sign_in_seconds
        )
        if self._wait_for_sign_in(browser, wait_seconds, fill_in=wait_for_person):
            return next(page for page in browser.pages if is_signed_in(page.url))
        raise SignInNeeded

    # King's single sign-on often signs in on its own; otherwise the person
    # finishes it (passkey, 2FA). The sign-in can open other tabs, so every
    # open page is watched. The username, and the password if King's asks
    # for one, are only ever typed on Microsoft's own sign-in page.
    def _wait_for_sign_in(self, browser: BrowserContext, wait_seconds: float, fill_in: bool) -> bool:
        deadline = time.monotonic() + wait_seconds
        filled: set[str] = set()
        while time.monotonic() < deadline:
            pages = [page for page in browser.pages if not page.is_closed()]
            if not pages:
                return False
            if any(is_signed_in(page.url) for page in pages):
                return True
            if fill_in and self._login is not None:
                for page in pages:
                    self._fill_in_microsoft_sign_in(page, filled)
            # a Playwright wait, not time.sleep: page addresses only update
            # while Playwright is running
            pages[0].wait_for_timeout(1500)
        return False

    def _fill_in_microsoft_sign_in(self, page: Page, filled: set[str]) -> None:
        if not page.url.startswith(MICROSOFT_SIGN_IN):
            return
        fields = {"username": ("input[name=loginfmt]", self._login.username)}
        fields["password"] = ("input[name=passwd]", self._login.password.get_secret_value())
        try:
            for field, (selector, value) in fields.items():
                if field not in filled and page.locator(selector).is_visible():
                    page.fill(selector, value)
                    page.keyboard.press("Enter")
                    filled.add(field)
                    logger.info("%s: filled in the %s; the rest is up to the person", self.name, field)
            # "Stay signed in?": yes, so the session outlives this run
            if page.locator("#KmsiDescription, #KmsiCheckboxField").count():
                page.locator("#idSIButton9").click(timeout=3000)
        except PlaywrightError:
            # the page moved on while being read; it's looked at again next round
            pass

    def _read_all_cards(self, page: Page) -> list[TargetConnectCard]:
        page.goto(JOBS_URL, wait_until="networkidle")
        page.get_by_text(str(RESULTS_PER_PAGE), exact=True).first.click()
        cards: dict[str, TargetConnectCard] = {}
        for page_number in range(1, self._settings.max_result_pages + 1):
            if page_number > 1:
                pager = page.locator("a[href='#']").filter(has_text=re.compile(rf"^\s*{page_number}\s*$"))
                if not pager.count():
                    break
                pager.first.click()
            # The list redraws in place: wait until it shows this page's range.
            # The site always shows a full range, even "301 to 400 of 371".
            first_result = (page_number - 1) * RESULTS_PER_PAGE + 1
            page.wait_for_function(
                "range => document.body.innerText.includes(range)",
                arg=f"{first_result} to {first_result + RESULTS_PER_PAGE - 1} of",
            )
            for raw_card in page.eval_on_selector_all(CARD_SELECTOR, READ_CARDS_SCRIPT):
                cards.setdefault(
                    raw_card["id"],
                    TargetConnectCard(
                        raw_card["id"],
                        raw_card["title"],
                        raw_card["organisation"],
                        raw_card["spec"],
                        raw_card["days"],
                    ),
                )
        logger.info("%s: %d jobs listed", self.name, len(cards))
        return list(cards.values())

    def _is_in_scope(self, raw_posting: TargetConnectCard) -> bool:
        return self._location_matcher.matches_any([read_spec(raw_posting.spec).location])

    def _to_fetched_job(self, raw_posting: TargetConnectCard) -> FetchedJob:
        spec = read_spec(raw_posting.spec)
        return FetchedJob(
            source=self.name,
            source_job_id=raw_posting.job_id,
            title=raw_posting.title,
            company_name=raw_posting.organisation,
            location=spec.location,
            url=ADVERT_URL.format(job_id=raw_posting.job_id),
            employment_type=spec.employment_type,
            salary_text=spec.salary,
            closes_at=closing_date(raw_posting.days, utc_now()),
        )


def is_signed_in(url: str) -> bool:
    return url.startswith(f"{SITE_URL}/") and "/unauth" not in url


@dataclass(frozen=True)
class CardSpec:
    employment_type: str | None
    location: str | None
    salary: str | None


# "Graduate Employment | UK - London +6 | £45,000 - £49,999"; any part can be
# missing, and "+6" means six more places the card doesn't name
def read_spec(spec: str) -> CardSpec:
    parts = [part.strip() for part in spec.split("|")] + [None, None, None]
    return CardSpec(parts[0] or None, parts[1] or None, parts[2] or None)


# "52 days to apply" -> 52 days from today; "Last day to apply" -> today
def closing_date(days_text: str, today: datetime) -> datetime | None:
    if re.search(r"last day", days_text, re.IGNORECASE):
        return today.replace(hour=23, minute=59, second=0, microsecond=0)
    match = DAYS_TO_APPLY.search(days_text)
    if match is None:
        return None
    return (today + timedelta(days=int(match.group(1)))).replace(hour=23, minute=59, second=0, microsecond=0)
