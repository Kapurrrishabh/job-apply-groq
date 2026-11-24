"""Employer job systems behind freehire postings. Greenhouse, Lever and Ashby forms are filled automatically;
other systems are assisted (you submit on the employer's site)."""
import re
from urllib.parse import parse_qs, urlparse
from .base import Adapter

AUTOMATED = {"greenhouse", "lever", "ashby"}


def form_url(browser, url: str, kind: str) -> str:
    if kind == "greenhouse":
        m = re.search(r"greenhouse\.io/(?!embed)([\w-]+)/jobs/(\d+)", url)
        if m: return f"https://job-boards.greenhouse.io/embed/job_app?for={m.group(1)}&token={m.group(2)}"
        q = parse_qs(urlparse(url).query)
        if "for" in q and "token" in q: return url
        browser.goto(url); browser.wait(4000)   # company careers page wrapping Greenhouse in an iframe
        frame = next((f for f in browser.p().frames if "greenhouse.io" in f.url and "job_app" in f.url), None)
        return frame.url if frame else url
    if kind == "ashby" and not url.split("?")[0].rstrip("/").endswith("/application"): return url.split("?")[0].rstrip("/") + "/application"
    if kind == "lever" and not url.split("?")[0].rstrip("/").endswith("/apply"): return url.split("?")[0].rstrip("/") + "/apply"
    return url


class EmployerSystem(Adapter):
    key = "ats"

    def fill_application(self, browser, job, form_ctx):
        from ..automation.forms import fill
        if job.ats not in AUTOMATED: raise NotImplementedError(f"{job.ats or 'this employer system'} is not supported for automation; you submit it")
        browser.goto(form_url(browser, job.apply_url or job.url, job.ats)); browser.wait(4000)
        return fill(browser, form_ctx)

    def submit_application(self, browser, job) -> tuple[str, str]:
        for label in ("Submit application", "Submit Application", "Submit"):
            if any(f["tag"] == "button" and f["text"].strip() == label for f in browser.fields()):
                browser.click(f"button:has-text('{label}') >> visible=true >> nth=-1", submit=True); browser.wait(5000)
                return browser.text(30000), browser.url()
        raise RuntimeError("Could not find the submit button on the employer's form")
