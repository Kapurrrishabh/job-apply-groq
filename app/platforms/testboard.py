"""Local test job board (tests/mock_board.py). Exercises the full automated path without touching real accounts."""
import os
import httpx
from .base import Adapter, clean, employment, parse_dt, work_mode


def base() -> str:
    return os.environ.get("JOBAPPLY_TESTBOARD", "")


class TestBoard(Adapter):
    key = "testboard"

    def search_jobs(self, browser, params: dict) -> list[dict]:
        if not base(): raise RuntimeError("The test board is not running (set JOBAPPLY_TESTBOARD)")
        r = httpx.get(f"{base()}/api/jobs", params={"q": params.get("query", "")}, timeout=10); r.raise_for_status()
        return [self.normalize_job(j) for j in r.json()]

    def check_availability(self, browser, job) -> bool | None:
        r = httpx.get(f"{base()}/api/jobs/{job.source_id}", timeout=10)
        return r.status_code == 200 and r.json().get("open", False)

    def normalize_job(self, j: dict) -> dict:
        return {"platform": "testboard", "source_id": str(j["id"]), "url": f"{base()}/jobs/{j['id']}", "apply_url": f"{base()}/jobs/{j['id']}/apply",
                "ats": "testboard", "title": clean(j["title"]), "company": clean(j["company"]), "location": clean(j["location"]),
                "work_mode": work_mode(j.get("location", "")), "employment_type": employment(j.get("type", "")), "experience": j.get("experience", ""),
                "salary_text": j.get("salary", ""), "description": j.get("description", ""), "skills": j.get("skills", []),
                "posted_at": parse_dt(j.get("posted_at")), "closing_at": None, "active": j.get("open", True), "raw": {}}

    def fill_application(self, browser, job, form_ctx):
        from ..automation.forms import fill
        browser.goto(job.apply_url); browser.wait(300)
        return fill(browser, form_ctx)

    def submit_application(self, browser, job) -> tuple[str, str]:
        browser.click("button:has-text('Submit application')", submit=True); browser.wait(500)
        return browser.text(20000), browser.url()
