"""Employer job systems behind freehire postings. Greenhouse, Lever and Ashby forms are filled automatically;
other systems are assisted (you submit on the employer's site)."""
import html
import re
from urllib.parse import parse_qs, urlparse
import httpx
from .base import Adapter, clean

AUTOMATED = {"greenhouse", "lever", "ashby"}
# Public job-board APIs these systems publish for showing an employer's jobs; no key needed.
POSTING_API = {"greenhouse": "https://boards-api.greenhouse.io/v1/boards/{org}/jobs/{id}", "lever": "https://api.lever.co/v0/postings/{org}/{id}",
               "ashby": "https://api.ashbyhq.com/posting-api/job-board/{org}"}


def parse_link(url: str) -> tuple[str, str, str] | None:
    """(system, employer, job id) for a Greenhouse, Lever or Ashby job link; None for anything else."""
    u = urlparse(url or ""); host = (u.hostname or "").lower(); parts = [p for p in u.path.split("/") if p]; q = parse_qs(u.query)
    if host == "greenhouse.io" or host.endswith(".greenhouse.io"):
        if "for" in q and "token" in q: return "greenhouse", q["for"][0], q["token"][0]
        if len(parts) >= 3 and parts[1] == "jobs" and parts[2].isdigit(): return "greenhouse", parts[0], parts[2]
    if host == "jobs.lever.co" and len(parts) >= 2: return "lever", parts[0], parts[1]
    if host == "jobs.ashbyhq.com" and len(parts) >= 2: return "ashby", parts[0], parts[1]
    return None


def _text(markup: str) -> str:
    t = re.sub(r"(?i)<(br|/p|/li|/h\d|/div)[^>]*>", "\n", html.unescape(markup or ""))
    return "\n".join(clean(line) for line in html.unescape(re.sub(r"<[^>]+>", " ", t)).splitlines() if clean(line))


def from_posting(kind: str, org: str, job_id: str, data: dict) -> dict:
    """Title, company, location and description out of a public job-board API reply."""
    name = org.replace("-", " ").replace("_", " ").title()
    if kind == "greenhouse":
        return {"title": data["title"], "company": data.get("company_name") or name, "location": (data.get("location") or {}).get("name", ""),
                "description": _text(data.get("content", ""))}
    if kind == "lever":
        lists = "\n".join(f"{x.get('text', '')}\n{_text(x.get('content', ''))}" for x in data.get("lists") or [])
        return {"title": data["text"], "company": name, "location": (data.get("categories") or {}).get("location", ""),
                "description": "\n".join(x for x in [data.get("descriptionPlain", ""), lists, data.get("additionalPlain", "")] if x)}
    job = next((j for j in data.get("jobs") or [] if j.get("id") == job_id), None)
    if not job: raise ValueError("That job is no longer on the employer's job board")
    return {"title": job["title"], "company": name, "location": job.get("location", ""), "description": job.get("descriptionPlain", "")}


def posting(url: str) -> dict:
    kind, org, job_id = parse_link(url) or (None, None, None)
    if not kind: raise ValueError("Not a Greenhouse, Lever or Ashby job link")
    r = httpx.get(POSTING_API[kind].format(org=org, id=job_id), timeout=15)
    if r.status_code == 404: raise ValueError("That job is no longer on the employer's job board")
    r.raise_for_status()
    return from_posting(kind, org, job_id, r.json())


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

    def check_availability(self, browser, job) -> bool | None:
        if not parse_link(job.url): return None
        try:
            posting(job.url); return True
        except ValueError:
            return False

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
