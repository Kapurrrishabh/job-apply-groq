"""freehire.me public API: discovery of postings from ~50 employer job systems (Greenhouse, Lever, Ashby, …)."""
import httpx
from .base import Adapter, clean, employment, parse_dt, work_mode

BASE = "https://freehire.me"
UA = {"User-Agent": "job-apply-groq/1.0 (+personal job search)"}


class Freehire(Adapter):
    key = "freehire"

    def _get(self, path: str, params=None) -> dict:
        r = httpx.get(BASE + path, params=params, headers=UA, timeout=30)
        if r.status_code == 404: return {}
        r.raise_for_status(); return r.json()

    def search_jobs(self, browser, params: dict) -> list[dict]:
        q = [("q", params.get("query", "")), ("limit", str(min(int(params.get("limit", 25)), 50))), ("offset", str(int(params.get("offset", 0)))),
             ("description_format", "text")]
        if params.get("days"): q.append(("posted_within_days", str(int(params["days"]))))
        if params.get("work_mode") in ("remote", "hybrid", "onsite"): q.append(("work_mode", params["work_mode"]))
        if params.get("company"): q.append(("company_slug", params["company"]))
        for c in params.get("countries", ["in"]): q.append(("countries", c))
        for c in params.get("cities", []): q.append(("cities", c))
        for lvl in params.get("seniority", []): q.append(("seniority", lvl))
        return [self.normalize_job(j) for j in (self._get("/api/v1/agent/jobs/search", q).get("data") or [])]

    def get_job_details(self, browser, job: dict) -> dict:
        d = self._get(f"/api/v1/jobs/{job['source_id']}")
        return self.normalize_job(d.get("data", d)) if d else {**job, "active": False}

    def check_availability(self, browser, job) -> bool | None:
        d = self._get(f"/api/v1/jobs/{job.source_id}")
        if not d: return False
        j = d.get("data", d)
        return not j.get("closed_at")

    def normalize_job(self, j: dict) -> dict:
        enr = j.get("enrichment") or {}
        years = enr.get("experience_years_min")
        return {"platform": "freehire", "source_id": j["public_slug"], "url": f"{BASE}/jobs/{j['public_slug']}",
                "apply_url": j.get("url") or "", "ats": (j.get("source") or "").lower(), "title": clean(j.get("title")),
                "company": clean(j.get("company")), "location": clean(j.get("location")),
                "work_mode": work_mode(" ".join(j.get("engagement") or []) + " " + clean(j.get("location"))),
                "employment_type": employment(" ".join(j.get("engagement") or []) + " " + clean(j.get("title"))),
                "experience": f"{years}+ years" if years is not None else "", "salary_text": "",
                "description": (j.get("description") or "")[:40000], "skills": list(j.get("skills") or [])[:40],
                "posted_at": parse_dt(j.get("posted_at")), "closing_at": None, "active": not j.get("closed_at"),
                "raw": {k: j.get(k) for k in ("source", "external_id", "countries", "cities", "apply_questions", "reality")}}
