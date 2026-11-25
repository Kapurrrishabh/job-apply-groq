"""Any other site, for people who switch it on: open the job, press its Apply button if there is one, then work through
the form step by step like a person would and submit. Buttons that move the form on are learned per site (see flows.py).
Whenever it isn't sure (an account wall, no form it recognises, a step it can't pass) it hands the job to you."""
import re
from urllib.parse import urlparse
from ..automation.browser import SUBMIT
from .base import Adapter

# APPLY and GOOGLE are also run inside the browser by Playwright, so they use flags rather than inline (?i).
APPLY = re.compile(r"^\s*(apply|apply now|apply for this (job|role|position)|apply online|i'?m interested)\s*$", re.I)
GOOGLE = re.compile(r"^\s*(continue|sign in|log in|sign up) with google\s*$", re.I)
ACCOUNT = re.compile(r"(?i)sign in to apply|log ?in to apply|create (an |your )?account|register to (apply|continue)|forgot (your )?password")
CLOSED = re.compile(r"(?i)no longer accepting applications|position has been filled|this job (is closed|has expired)|job is no longer available")
GOOGLE_STOP = re.compile(r"(?i)verify it'?s you|2-step|couldn'?t sign you in|this browser or app may not be secure|enter your password")
EXTRA_ACCESS = re.compile(r"(?i)google drive|gmail|calendar|contacts|youtube|photos|see, edit|delete|manage your|send email")
MAX_STEPS = 10


class NeedsYou(Exception):
    """The site needs something only you can do; the application waits for you with this message."""


class GenericSite(Adapter):
    key = "other"

    def check_availability(self, browser, job) -> bool | None:
        browser.goto(job.apply_url or job.url); browser.wait(2500)
        return not CLOSED.search(browser.text(20000))

    def fill_application(self, browser, job, form_ctx):
        from ..automation.forms import FillResult, fill
        browser.goto(job.apply_url or job.url); browser.wait(2500)
        if not self._has_form(browser): self._follow_apply(browser)
        if not self._has_form(browser) and form_ctx.use_google and self._visible(browser, GOOGLE).count():
            self._sign_in_with_google(browser, form_ctx.profile.personal.email)
            if not self._has_form(browser): self._follow_apply(browser)
        if not self._has_form(browser):
            if ACCOUNT.search(browser.text(20000)):
                raise NeedsYou("This site wants you to sign in or create an account first. Do that in the app's browser window, "
                               "then press Retry, or finish there and press 'I submitted it'.")
            raise NeedsYou("Couldn't find an application form on this page. Finish it in the app's browser window, then press 'I submitted it'.")
        res, seen = FillResult(), set()
        for _ in range(MAX_STEPS):
            step = fill(browser, form_ctx)
            res.answers += [a for a in step.answers if a["question"] not in seen]; seen |= {a["question"] for a in step.answers}
            res.resume_uploaded |= step.resume_uploaded
            if step.missing:
                res.missing = step.missing; return res
            if self._submit_buttons(browser): return res
            self._next_step(browser, form_ctx.navigator)
        raise NeedsYou("The form has more steps than expected. Finish it in the app's browser window, then press 'I submitted it'.")

    def _visible(self, browser, pattern):
        return browser.p().locator("a, button, [role=button]").filter(has_text=pattern).locator("visible=true")

    def _follow_apply(self, browser):
        target = self._visible(browser, APPLY)
        if not target.count(): return
        href = target.first.get_attribute("href")   # Apply links often open a new tab, so follow them in this one
        if href and not href.startswith(("#", "javascript:")): browser.goto(target.first.evaluate("a => a.href"))
        else: target.first.click(timeout=15000)
        browser.wait(2500)

    def _has_form(self, browser) -> bool:
        """An application form, not a sign-in page: a page asking for a password is an account wall."""
        fields = [f for f in browser.fields() if f["tag"] in ("input", "select", "textarea") and f["type"] not in ("search", "submit", "button", "hidden")]
        if any(f["type"] == "password" for f in fields): return False
        return any(f["type"] in ("file", "email") for f in fields) or len(fields) >= 3

    def _submit_buttons(self, browser) -> list[dict]:
        return [f for f in browser.fields() if (f["tag"] == "button" or f["type"] == "submit") and SUBMIT.search(f["text"] or f["value"] or "")]

    def _next_step(self, browser, navigator):
        buttons = {(f["text"] or f["value"] or "").strip(): f["sel"] for f in browser.fields() if f["tag"] == "button" or f["type"] in ("button", "submit")}
        host = urlparse(browser.url()).hostname or ""
        heading = browser.p().evaluate("() => [...document.querySelectorAll('h1, h2, h3')].filter(h => h.offsetParent).map(h => h.innerText).join(' / ')")
        label, how = navigator.choose(host, list(buttons), heading) if navigator else (None, "no navigator")
        if not label: raise NeedsYou(f"Couldn't tell how to continue this form ({how}). Finish it in the app's browser window, then press 'I submitted it'.")
        before = (browser.url(), browser.text(4000))
        browser.click(buttons[label]); browser.wait(2000)
        if (browser.url(), browser.text(4000)) == before:
            raise NeedsYou(f"The form didn't move on after '{label}'; a field may be invalid. Check it in the app's browser window, then press Retry.")
        navigator.remember(host, label)

    def _sign_in_with_google(self, browser, email: str):
        """Click 'Continue with Google' and pick your account. Never types a password; stops for checks or extra access."""
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        page = browser.p()
        try:
            with page.context.expect_page(timeout=4000) as popup:
                self._visible(browser, GOOGLE).first.click(timeout=15000)
            google = popup.value
        except PlaywrightTimeout:
            google = page   # the site sent this tab to Google instead of opening a window
        for _ in range(6):
            google.wait_for_load_state("domcontentloaded"); google.wait_for_timeout(1200)
            if google.is_closed() or not (urlparse(google.url).hostname or "").endswith("accounts.google.com"): break
            text = google.inner_text("body")
            if google.locator("input[type=password]").count() or GOOGLE_STOP.search(text):
                raise NeedsYou("Google wants you to sign in or confirm it's you. Do that in the app's browser window, then press Retry.")
            if EXTRA_ACCESS.search(text):
                raise NeedsYou("The site asks for more of your Google account than your name and email. Decide in the app's browser window, then press Retry.")
            account = google.locator(f"[data-identifier='{email}']")
            if not account.count() and google.locator("[data-identifier]").count() == 1: account = google.locator("[data-identifier]")
            if account.count():
                account.first.click(); continue
            allow = google.get_by_role("button", name=re.compile(r"^(continue|allow)$", re.I))
            if allow.count():
                allow.first.click(); continue
            raise NeedsYou("Google showed a step the app doesn't recognise. Finish it in the app's browser window, then press Retry.")
        else:
            raise NeedsYou("Google sign-in didn't finish. Complete it in the app's browser window, then press Retry.")
        if google is not page and not google.is_closed(): google.wait_for_event("close", timeout=10000)
        page.wait_for_timeout(2000)

    def submit_application(self, browser, job) -> tuple[str, str]:
        buttons = self._submit_buttons(browser)
        if not buttons: raise NeedsYou("Couldn't find the form's submit button; nothing was sent. Check the form in the app's browser window.")
        browser.click(buttons[-1]["sel"], submit=True); browser.wait(4000)
        return browser.text(30000), browser.url()

    def verify_submission(self, job, text, url):
        from ..automation.evidence import verify
        return verify("generic", text, url)
