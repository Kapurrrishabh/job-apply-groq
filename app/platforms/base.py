"""The adapter interface every platform implements, and the common job shape they all return."""
import re
from datetime import datetime, timezone
from ..automation.evidence import Verdict, verify


def clean(s) -> str:
    return " ".join(str(s or "").split())


def parse_dt(v) -> datetime | None:
    if not v: return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def work_mode(text: str) -> str:
    t = (text or "").lower()
    return "remote" if "remote" in t else "hybrid" if "hybrid" in t else "onsite" if re.search(r"on.?site|in office|work from office", t) else ""


def employment(text: str) -> str:
    t = (text or "").lower()
    return "internship" if "intern" in t else "contract" if "contract" in t else "part-time" if "part-time" in t or "part time" in t else "full-time" if "full" in t else ""


class Adapter:
    key = ""

    def search_jobs(self, browser, params: dict) -> list[dict]:
        raise NotImplementedError(f"{self.key} has no automated search; import jobs instead")

    def get_job_details(self, browser, job: dict) -> dict:
        return job

    def normalize_job(self, raw: dict) -> dict:
        raise NotImplementedError

    def check_availability(self, browser, job) -> bool | None:
        return None                      # None = unknown, not "closed"

    def fill_application(self, browser, job, form_ctx):
        raise NotImplementedError(f"{self.key} applications are assisted: you submit them yourself")

    def submit_application(self, browser, job) -> tuple[str, str]:
        raise NotImplementedError

    def verify_submission(self, job, text: str, url: str) -> Verdict:
        return verify(job.ats or job.platform, text, url)
