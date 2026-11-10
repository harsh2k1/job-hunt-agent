from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import BrowserContext, sync_playwright


# A real browser with its own saved profile per portal, so a sign-in done
# once (passkey, 2FA) keeps working on later runs until the portal ends the
# session. Headless for daily runs; visible only when a person must sign in.
@contextmanager
def open_portal_browser(profile_dir: Path, headless: bool) -> Iterator[BrowserContext]:
    profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile_dir), headless=headless, viewport={"width": 1280, "height": 900}
        )
        try:
            yield context
        finally:
            context.close()
