"""Any other site, for people who switch it on: open the job, press its Apply button if there is one, fill the form
like a person would and submit. Sites differ a lot, so whenever it isn't sure (an account wall, no form it recognises,
no submit button) it stops and hands the job to you."""
import re
from ..automation.browser import SUBMIT
from .base import Adapter

APPLY = re.compile(r"^\s*(apply|apply now|apply for this (job|role|position)|apply online|i'?m interested)\s*$", re.I)   # used in the browser: no inline flags
ACCOUNT = re.compile(r"(?i)sign in to apply|log ?in to apply|create (an |your )?account|register to (apply|continue)|forgot (your )?password")
CLOSED = re.compile(r"(?i)no longer accepting applications|position has been filled|this job (is closed|has expired)|job is no longer available")


class NeedsYou(Exception):
    """The site needs something only you can do; the application waits for you with this message."""


class GenericSite(Adapter):
    key = "other"

    def check_availability(self, browser, job) -> bool | None:
        browser.goto(job.apply_url or job.url); browser.wait(2500)
        return not CLOSED.search(browser.text(20000))

    def fill_application(self, browser, job, form_ctx):
        from ..automation.forms import fill
        browser.goto(job.apply_url or job.url); browser.wait(2500)
        if not self._has_form(browser):
            target = browser.p().locator("a, button").filter(has_text=APPLY).locator("visible=true")
            if target.count():   # an Apply link opens the form, often in a new tab, so follow it in this one instead
                href = target.first.get_attribute("href")
                if href and not href.startswith(("#", "javascript:")): browser.goto(target.first.evaluate("a => a.href"))
                else: target.first.click(timeout=15000)
                browser.wait(2500)
        if not self._has_form(browser):
            if ACCOUNT.search(browser.text(20000)):
                raise NeedsYou("This site wants you to sign in or create an account first. Do that in the app's browser window, "
                               "then press Retry, or finish there and press 'I submitted it'.")
            raise NeedsYou("Couldn't find an application form on this page. Finish it in the app's browser window, then press 'I submitted it'.")
        return fill(browser, form_ctx)

    def _has_form(self, browser) -> bool:
        """An application form, not a sign-in page: a page asking for a password is an account wall."""
        fields = [f for f in browser.fields() if f["tag"] in ("input", "select", "textarea") and f["type"] not in ("search", "submit", "button", "hidden")]
        if any(f["type"] == "password" for f in fields): return False
        return any(f["type"] in ("file", "email") for f in fields) or len(fields) >= 3

    def submit_application(self, browser, job) -> tuple[str, str]:
        buttons = [f for f in browser.fields() if (f["tag"] == "button" or f["type"] == "submit") and SUBMIT.search(f["text"] or f["value"] or "")]
        if not buttons: raise NeedsYou("Couldn't find the form's submit button; nothing was sent. Check the form in the app's browser window.")
        browser.click(buttons[-1]["sel"], submit=True); browser.wait(4000)
        return browser.text(30000), browser.url()

    def verify_submission(self, job, text, url):
        from ..automation.evidence import verify
        return verify("generic", text, url)
