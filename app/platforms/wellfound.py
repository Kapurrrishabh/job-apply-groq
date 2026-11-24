"""Wellfound: import the job page you are viewing (one page, on your click), and apply with your profile resume.
Terms: automated use is allowed at human-like load (§III.6); scraping is not (§III.4)."""
import re
import time
from .base import Adapter, clean, employment, work_mode


class Wellfound(Adapter):
    key = "wellfound"

    def import_current_page(self, browser) -> dict:
        url = browser.url()
        m = re.search(r"wellfound\.com/jobs/(\d+[-\w]*)", url)
        if not m: raise ValueError("Open a Wellfound job page (wellfound.com/jobs/…) in the app's browser first")
        t = browser.text(40000); f = " ".join(t.split())
        title = re.search(r"\n([^\n]{3,120}) at ([^\n]{2,80})\n", t)
        sal = re.search(r"₹[\d.,]+[LK]?\s*–\s*₹[\d.,]+[LK]?", f)
        loc = re.search(r"Location ([A-Za-z ,()]+?) (?:Company size|Remote work policy)", f)
        return self.normalize_job({"slug": m.group(1), "url": url.split("?")[0], "title": title.group(1) if title else m.group(1),
                                   "company": title.group(2) if title else "", "location": loc.group(1) if loc else "",
                                   "salary": sal.group(0) if sal else "", "description": t[:40000]})

    def normalize_job(self, raw: dict) -> dict:
        d = raw.get("description", "")
        return {"platform": "wellfound", "source_id": raw["slug"], "url": raw["url"], "apply_url": raw["url"], "ats": "wellfound",
                "title": clean(raw["title"]), "company": clean(raw["company"]), "location": clean(raw["location"]),
                "work_mode": work_mode(d[:3000]), "employment_type": employment(d[:3000] + " " + raw["title"]), "experience": "",
                "salary_text": raw.get("salary", ""), "description": d, "skills": [], "posted_at": None, "closing_at": None, "active": True,
                "raw": {"imported": "page"}}

    def check_availability(self, browser, job) -> bool | None:
        browser.goto(job.url); browser.wait(4000)
        t = browser.text(20000)
        return False if re.search(r"(?i)no longer accepting|this job is closed|job not found", t) else True

    def fill_application(self, browser, job, form_ctx):
        from ..automation.forms import FillResult
        browser.goto(job.url); browser.wait(5000)
        browser.click("button:has-text('Apply') >> visible=true >> nth=0"); browser.wait(3500)
        t = browser.text(80000)
        if "YOUR APPLICATION" not in t: raise RuntimeError("Wellfound did not open its apply panel (already applied, or applies on the company site)")
        if "not accepting applications from your current location" in t: raise PermissionError("The employer does not accept your location")
        res = FillResult()
        for f in browser.fields():
            if not f["name"].startswith("customQuestionAnswers"): continue
            q = clean(f["label"] or f["aria"]).rstrip("*").strip()
            from ..automation.forms import decide
            a, src = (form_ctx.cover_text, "cover letter") if "interests you" in q.lower() else decide(q, f.get("options"), form_ctx)
            if not a: res.missing.append({"question": q, "options": f.get("options"), "sensitive": False}); continue
            (browser.select if f["tag"] == "select" else browser.fill)(f["sel"], a)
            res.answers.append({"question": q, "answer": a, "source": src, "field_type": f["tag"]})
        res.resume_uploaded = True   # Wellfound sends the resume on your Wellfound profile
        return res

    def submit_application(self, browser, job) -> tuple[str, str]:
        browser.click("button:has-text('Send application') >> visible=true", submit=True); browser.wait(4000)
        time.sleep(1)
        browser.goto("https://wellfound.com/jobs/applications"); browser.wait(5000)
        return browser.text(30000), browser.url()

    def verify_submission(self, job, text, url):
        """Evidence is your Wellfound applications list showing this company with this role."""
        from ..automation.evidence import Verdict
        lines = [l.strip() for l in text.splitlines()]
        for i, l in enumerate(lines):
            if job.company and l.lower() == job.company.lower() and any(job.title.lower()[:25] in x.lower() for x in lines[i:i + 3]):
                return Verdict(True, f"your Wellfound applications list shows {job.company}: {job.title}")
        return Verdict(False, "this job is not on your Wellfound applications list yet")
