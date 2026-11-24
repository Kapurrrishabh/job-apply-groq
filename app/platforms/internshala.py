"""Internshala: import the internship or job page you are viewing (one page, on your click), and apply to jobs you
picked with the resume on your Internshala profile. Terms (checked 2026-11-24) don't address automation but block
accounts that send irrelevant applications, so the app only applies where you authorized it, one at a time."""
import re
from urllib.parse import urlparse
from .base import Adapter, clean, employment, work_mode
from .testboard import base as testboard

DETAIL = re.compile(r"/(internship|job)s?/details?/([\w-]+)")
CLOSED = re.compile(r"(?i)no longer accepting applications|applications? (are|is) closed|internship is no longer available|job is no longer available")


def _is_internshala(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host == "internshala.com" or host.endswith(".internshala.com") or bool(testboard() and url.startswith(testboard()))


class Internshala(Adapter):
    key = "internshala"

    def import_current_page(self, browser) -> dict:
        url = browser.url().split("?")[0]
        m = DETAIL.search(url)
        if not (m and _is_internshala(url)): raise ValueError("Open an Internshala internship or job page (internshala.com/internship/detail/…) in the app's browser first")
        t = browser.text(40000)
        lines = [clean(x) for x in t.splitlines() if clean(x)]
        title = clean(browser.p().evaluate("() => (document.querySelector('h1') || {}).innerText || ''"))
        after = lambda label: next((lines[i + 1] for i, x in enumerate(lines[:-1]) if x.upper() == label), "")
        at = lines.index(title) if title in lines else -1
        return self.normalize_job({"slug": m.group(2), "kind": m.group(1), "url": url, "title": title or m.group(2).replace("-", " "),
                                   "company": lines[at + 1] if 0 <= at < len(lines) - 1 else "",
                                   "location": next((lines[i - 1] for i, x in enumerate(lines) if x.upper() == "START DATE" and i), ""),
                                   "salary": after("STIPEND") or after("CTC (ANNUAL)") or after("SALARY"), "description": t[:40000]})

    def normalize_job(self, raw: dict) -> dict:
        d = raw.get("description", "")
        return {"platform": "internshala", "source_id": raw["slug"], "url": raw["url"], "apply_url": raw["url"], "ats": "internshala",
                "title": clean(raw["title"]), "company": clean(raw["company"]), "location": clean(raw["location"]),
                "work_mode": work_mode(d[:3000] + " " + raw["location"].replace("Work from home", "remote")),
                "employment_type": "internship" if raw.get("kind") == "internship" else employment(d[:3000] + " " + raw["title"]),
                "experience": "", "salary_text": clean(raw.get("salary")), "description": d, "skills": [], "posted_at": None, "closing_at": None,
                "active": True, "raw": {"imported": "page"}}

    def check_availability(self, browser, job) -> bool | None:
        browser.goto(job.url); browser.wait(3000)
        return not CLOSED.search(browser.text(20000))

    def fill_application(self, browser, job, form_ctx):
        from ..automation.forms import fill
        browser.goto(job.url); browser.wait(3000)
        if re.search(r"(?i)\balready applied\b", browser.text(20000)):
            raise RuntimeError("Internshala shows you already applied to this one; nothing was sent")
        browser.click("button:has-text('Apply now') >> visible=true >> nth=0"); browser.wait(3000)
        if "Proceed to application" in browser.text(20000):
            browser.click("text='Proceed to application' >> visible=true"); browser.wait(2500)
        answered = []
        editor = ".ql-editor >> visible=true"
        if form_ctx.cover_text and browser.p().locator(editor).count():
            browser.fill(f"{editor} >> nth=0", form_ctx.cover_text)   # "Why should you be hired?" is a rich-text box, not a form field
            answered.append({"question": "Why should you be hired for this role?", "answer": form_ctx.cover_text, "source": "cover letter", "field_type": "editor"})
        res = fill(browser, form_ctx)
        res.answers = answered + res.answers
        res.resume_uploaded = True   # Internshala sends the resume on your Internshala profile
        return res

    def submit_application(self, browser, job) -> tuple[str, str]:
        browser.click("button:has-text('Submit') >> visible=true >> nth=-1", submit=True); browser.wait(3000)
        after = browser.text(20000)
        u = urlparse(job.url)
        browser.goto(f"{u.scheme}://{u.netloc}/student/applications"); browser.wait(3000)
        return after + "\n" + browser.text(40000), browser.url()

    def verify_submission(self, job, text, url):
        """Evidence is your Internshala applications list showing this company with this role."""
        from ..automation.evidence import Verdict
        lines = [l.strip().lower() for l in text.splitlines() if l.strip()]
        for i, l in enumerate(lines):
            if job.company and l == job.company.lower() and any(job.title.lower()[:25] in x for x in lines[max(0, i - 2):i + 3]):
                return Verdict(True, f"your Internshala applications list shows {job.company}: {job.title}")
        return Verdict(False, "this job is not on your Internshala applications list yet")
