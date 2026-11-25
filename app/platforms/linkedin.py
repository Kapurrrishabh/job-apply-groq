"""LinkedIn Easy Apply, only for people who switch it on in Settings. LinkedIn's User Agreement §8.2 forbids bots and
LinkedIn may restrict accounts that use them, so it is off by default. Jobs are imported one page at a time, never searched,
and the window is driven like a person would: no hiding that it is automated."""
import re
from urllib.parse import parse_qs, urlparse
from .base import Adapter, clean, employment, work_mode
from .testboard import base as testboard

DIALOG = "[role=dialog]"
CLOSED = re.compile(r"(?i)no longer accepting applications")
APPLIED = re.compile(r"(?i)\bapplied \d+ \w+ ago\b|application submitted")
STEP_BUTTONS = ("Next", "Review", "Continue to next step", "Review your application")


def job_id(url: str) -> str | None:
    u = urlparse(url or "")
    host = u.hostname or ""
    if not (host == "linkedin.com" or host.endswith(".linkedin.com") or (testboard() and url.startswith(testboard()))): return None
    m = re.search(r"/jobs/view/(\d+)", u.path)
    return m.group(1) if m else (parse_qs(u.query).get("currentJobId") or [None])[0]


class LinkedIn(Adapter):
    key = "linkedin"

    def import_current_page(self, browser) -> dict:
        jid = job_id(browser.url())
        if not jid: raise ValueError("Open a LinkedIn job (linkedin.com/jobs/view/…) in the app's browser first")
        t = browser.text(40000)
        lines = [clean(x) for x in t.splitlines() if clean(x)]
        title = clean(browser.p().evaluate("() => (document.querySelector('h1') || {}).innerText || ''"))
        at = lines.index(title) if title in lines else -1
        company = lines[at + 1] if 0 <= at < len(lines) - 1 else ""
        place = lines[at + 2] if 0 <= at < len(lines) - 2 else ""
        url = browser.url().split("?")[0] if "/jobs/view/" in browser.url() else f"https://www.linkedin.com/jobs/view/{jid}/"
        return self.normalize_job({"id": jid, "url": url, "title": title or f"LinkedIn job {jid}", "company": company,
                                   "location": place.split("·")[0], "description": t[:40000]})

    def normalize_job(self, raw: dict) -> dict:
        d = raw.get("description", "")
        return {"platform": "linkedin", "source_id": raw["id"], "url": raw["url"], "apply_url": raw["url"], "ats": "linkedin",
                "title": clean(raw["title"]), "company": clean(raw["company"]), "location": clean(raw["location"]),
                "work_mode": work_mode(d[:3000]), "employment_type": employment(d[:3000] + " " + raw["title"]), "experience": "", "salary_text": "",
                "description": d, "skills": [], "posted_at": None, "closing_at": None, "active": True, "raw": {"imported": "page"}}

    def check_availability(self, browser, job) -> bool | None:
        browser.goto(job.url); browser.wait(3000)
        return not CLOSED.search(browser.text(20000))

    def _button(self, browser, labels) -> str | None:
        found = {f["text"].strip(): f["sel"] for f in browser.fields(DIALOG) if f["tag"] == "button"}
        return next((found[l] for l in labels if l in found), None)

    def fill_application(self, browser, job, form_ctx):
        from ..automation.forms import FillResult, fill
        browser.goto(job.url); browser.wait(3000)
        head = browser.text(6000)
        if APPLIED.search(head): raise RuntimeError("LinkedIn shows you already applied to this one; nothing was sent")
        if not browser.p().locator("button:has-text('Easy Apply') >> visible=true").count():
            raise RuntimeError("This LinkedIn job applies on the company's own site. Copy that link and paste it on Find jobs: "
                               "Greenhouse, Lever and Ashby links are applied to automatically")
        browser.click("button:has-text('Easy Apply') >> visible=true >> nth=0"); browser.wait(2000)
        res, seen = FillResult(), set()
        for _ in range(12):
            if not browser.p().locator(f"{DIALOG} >> visible=true").count(): raise RuntimeError("LinkedIn's Easy Apply window did not open")
            step = fill(browser, form_ctx, within=DIALOG)
            res.answers += [a for a in step.answers if a["question"] not in seen]; seen |= {a["question"] for a in step.answers}
            res.resume_uploaded |= step.resume_uploaded
            if step.missing:
                res.missing = step.missing; return res
            if self._button(browser, ("Submit application",)):
                res.resume_uploaded = True   # LinkedIn attaches the resume chosen in the window, or the one uploaded above
                return res
            nxt = self._button(browser, STEP_BUTTONS)
            if not nxt: raise RuntimeError("Couldn't find Next or Review in LinkedIn's Easy Apply window")
            browser.click(nxt); browser.wait(1500)
            error = browser.p().locator(f"{DIALOG} [role=alert], {DIALOG} .artdeco-inline-feedback--error")
            if error.count() and error.first.inner_text().strip():
                raise RuntimeError(f"LinkedIn didn't accept an answer: {error.first.inner_text().strip()[:200]}")
        raise RuntimeError("LinkedIn's Easy Apply window had more steps than expected")

    def submit_application(self, browser, job) -> tuple[str, str]:
        browser.click(self._button(browser, ("Submit application",)), submit=True); browser.wait(3000)
        return browser.text(20000), browser.url()
