"""Job discovery, import, normalisation into the Job table, de-duplication, filters and ranking."""
import re
from sqlalchemy import select
from . import db, events, gates, profile as profiles, ranking
from .platforms import PLATFORMS, adapters


def _key(company: str, title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", f"{company} {title}".lower()).strip()


def upsert(s, user_id: int, j: dict) -> tuple[db.Job, bool]:
    row = s.scalar(select(db.Job).where(db.Job.user_id == user_id, db.Job.platform == j["platform"], db.Job.source_id == j["source_id"]))
    new = row is None
    if new:
        row = db.Job(user_id=user_id, **{k: v for k, v in j.items() if hasattr(db.Job, k)}); s.add(row)
    else:
        for k, v in j.items():
            if hasattr(db.Job, k) and k not in ("bookmarked",) and (v or k == "active"): setattr(row, k, v)
        row.last_verified_at = db.now()
    s.flush()
    twin = s.scalar(select(db.Job.id).where(db.Job.user_id == user_id, db.Job.id != row.id, db.Job.raw["dedup"].as_string() == _key(row.company, row.title)))
    row.raw = {**(row.raw or {}), "dedup": _key(row.company, row.title), **({"duplicate_of": twin} if twin else {})}
    p, _ = profiles.current(s, user_id)
    row.gates = gates.evaluate({"title": row.title, "company": row.company, "location": row.location, "description": row.description,
                                "salary": row.salary_text, "employment": row.employment_type}, p.preferences.model_dump(), [l.name for l in p.languages])
    return row, new


def search_params(platform: str, params: dict) -> dict:
    """Turn the search form's location into the platform's filters."""
    p = dict(params); loc = (p.get("location") or "").strip()
    if platform == "freehire":
        if loc.lower() == "remote": p["work_mode"] = "remote"
        elif loc and loc.lower() != "india": p["cities"] = [loc.split(",")[0].strip()]
    return p


def search(s, user_id: int, platform: str, params: dict, browser=None, execution_id=None) -> list[db.Job]:
    pol = PLATFORMS[platform]
    params = search_params(platform, params)
    if pol.discovery not in ("api", "test"): raise PermissionError(f"{pol.name} jobs are imported, not searched ({pol.terms_note})")
    ad, _ = adapters()
    with events.step(s, user_id, "search", f"Searching {pol.name} for '{params.get('query', '')}'", platform=platform, skill="freehire-search" if platform == "freehire" else "", execution_id=execution_id):
        found = ad[platform].search_jobs(browser, params)
    rows, fresh = [], 0
    for j in found:
        if gates.looks_senior(j["title"]) and params.get("skip_senior", True): continue
        row, new = upsert(s, user_id, j); rows.append(row); fresh += new
    s.add(db.SavedSearch(user_id=user_id, name=params.get("query", ""), platform=platform, params=params, saved=False,
                         last_run_at=db.now(), result_count=len(rows)))
    events.emit(s, user_id, f"{len(found)} jobs found, {len(rows)} kept ({fresh} new); {sum(r.gates.get('verdict') == 'pass' for r in rows)} pass your filters",
                operation="search", platform=platform, level="success", status="completed", execution_id=execution_id)
    return rows


def import_manual(s, user_id: int, fields: dict) -> db.Job:
    ad, _ = adapters()
    row, _ = upsert(s, user_id, ad["manual"].normalize_job(fields))
    events.emit(s, user_id, f"Imported {row.title} at {row.company or 'unknown company'} ({PLATFORMS.get(row.platform, PLATFORMS['other']).name})",
                operation="import", platform=row.platform, level="success", status="completed")
    return row


def get(s, user_id: int, job_id: int) -> db.Job:
    j = s.scalar(select(db.Job).where(db.Job.id == job_id, db.Job.user_id == user_id))
    if not j: raise LookupError("Job not found")
    return j


def latest_score(s, user_id: int, job_id: int) -> db.JobScore | None:
    return s.scalar(select(db.JobScore).where(db.JobScore.user_id == user_id, db.JobScore.job_id == job_id).order_by(db.JobScore.id.desc()))


def rank(s, user_id: int, job: db.Job, use_ai: bool = True, execution_id=None) -> db.JobScore:
    from .ai import service
    from .ai.provider import LLMError
    p, version = profiles.current(s, user_id)
    jd = {"title": job.title, "company": job.company, "location": job.location, "description": job.description,
          "employment_type": job.employment_type, "skills_canonical": [x for x in ranking.skills_in(" ".join(job.skills or []))]}
    det = ranking.deterministic(jd, p)
    sem = None
    if use_ai:
        try:
            sem = ranking.semantic(s, user_id, jd, p)
        except LLMError as e:
            events.emit(s, user_id, f"Groq unavailable for {job.title} ({e}); ranked on structured data only", operation="rank", level="warn", execution_id=execution_id)
    weights = {**ranking.DEFAULT_WEIGHTS, **(service.settings(s, user_id).weights or {})}
    res = ranking.combine(det, sem, weights)
    row = db.JobScore(user_id=user_id, job_id=job.id, profile_version=version, score=res.match_score, confidence=res.confidence,
                      recommendation=res.recommendation, result=res.model_dump())
    s.add(row); s.flush()
    return row
