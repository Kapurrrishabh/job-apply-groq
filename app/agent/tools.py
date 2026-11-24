"""Tools the agent may call. Each has a typed input model, a permission level, and an implementation that goes
through the normal services (with their access checks). Submitting applications is deliberately not a tool."""
from typing import Literal
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from .. import db, jobs, letters, profile as profiles
from ..platforms import PLATFORMS


class SearchJobs(BaseModel):
    platform: Literal["freehire"] = "freehire"
    query: str = Field(min_length=2, max_length=120)
    location: str = Field(default="India", max_length=80)
    days: int = Field(default=14, ge=1, le=60)


class JobIds(BaseModel):
    job_ids: list[int] = Field(min_length=1, max_length=25)


class JobId(BaseModel):
    job_id: int


class Letter(BaseModel):
    job_id: int
    style: Literal["professional", "concise", "technical", "internship", "startup", "experienced"] = "professional"


class ListJobs(BaseModel):
    min_score: int = Field(default=0, ge=0, le=100)
    verdict: Literal["any", "pass", "flag", "fail"] = "pass"
    limit: int = Field(default=15, ge=1, le=50)


class Queries(BaseModel):
    goal: str = Field(max_length=300)


class Empty(BaseModel):
    pass


def _job_line(j: db.Job, sc) -> dict:
    return {"id": j.id, "title": j.title, "company": j.company, "location": j.location, "platform": j.platform,
            "filters": j.gates.get("verdict"), "score": sc.score if sc else None}


def t_search(s, uid, a: SearchJobs, exid):
    rows = jobs.search(s, uid, a.platform, {"query": a.query, "location": a.location, "days": a.days}, execution_id=exid)
    return {"jobs": [_job_line(j, None) for j in rows[:15]]}


def t_list(s, uid, a: ListJobs, exid):
    out = []
    for j in s.scalars(select(db.Job).where(db.Job.user_id == uid).order_by(db.Job.id.desc()).limit(200)):
        sc = jobs.latest_score(s, uid, j.id)
        if (a.verdict == "any" or j.gates.get("verdict") == a.verdict) and (sc.score if sc else 0) >= a.min_score: out.append(_job_line(j, sc))
    return {"jobs": out[: a.limit]}


def t_rank(s, uid, a: JobIds, exid):
    res = []
    for jid in a.job_ids:
        sc = jobs.rank(s, uid, jobs.get(s, uid, jid), execution_id=exid)
        res.append({"job_id": jid, "score": sc.score, "recommendation": sc.recommendation, "gaps": sc.result.get("gaps", [])[:2]})
    return {"ranked": res}


def t_details(s, uid, a: JobId, exid):
    j = jobs.get(s, uid, a.job_id); sc = jobs.latest_score(s, uid, j.id)
    return {**_job_line(j, sc), "description": j.description[:1500], "reasons": j.gates.get("reasons", []),
            "ranking": sc.result if sc else None}


def t_letter(s, uid, a: Letter, exid):
    v = letters.generate(s, uid, jobs.get(s, uid, a.job_id), a.style)
    return {"version": v.number, "warnings": v.issues, "preview": v.paragraphs[0][:300] if v.paragraphs else ""}


def t_select(s, uid, a: JobIds, exid):
    from ..automation.applications import select_jobs
    apps = select_jobs(s, uid, a.job_ids)
    return {"selected": [x.id for x in apps], "note": "Selected for review. Applications are only sent when the user presses Apply."}


def t_profile(s, uid, a: Empty, exid):
    p, v = profiles.current(s, uid)
    pct, missing = profiles.completeness(p)
    return {"version": v, "completeness": pct, "missing": missing, "roles": p.preferences.roles, "locations": p.preferences.locations,
            "skills": sorted({x for f in type(p.skills).model_fields for x in getattr(p.skills, f)})[:40]}


def t_queries(s, uid, a: Queries, exid):
    p, _ = profiles.current(s, uid)
    roles = p.preferences.roles or ["Software Engineer"]
    return {"suggestions": [f"{r}" for r in roles[:6]], "note": "Search freehire with each suggestion; LinkedIn, Naukri and Internshala jobs are imported manually."}


TOOLS = {
    "search_jobs": ("Search the freehire.me public job API and save the results.", SearchJobs, "write", t_search),
    "list_jobs": ("List saved jobs, filtered by filter verdict and minimum score.", ListJobs, "read", t_list),
    "rank_jobs": ("Rank saved jobs against the candidate profile (hybrid scoring).", JobIds, "write", t_rank),
    "job_details": ("Details, filter reasons and ranking for one saved job.", JobId, "read", t_details),
    "write_cover_letter": ("Draft a cover letter for a saved job (saved as a new version).", Letter, "write", t_letter),
    "select_jobs": ("Put jobs in the application queue for the user to review. Does not apply.", JobIds, "write", t_select),
    "profile_summary": ("Profile completeness, desired roles, locations and skills.", Empty, "read", t_profile),
    "refine_queries": ("Suggest search queries for the user's goal.", Queries, "read", t_queries),
}


def schemas() -> list[dict]:
    from ..ai.service import strict_schema
    return [{"name": n, "description": d, "parameters": strict_schema(m)} for n, (d, m, _, _) in TOOLS.items()]


def call(s, uid: int, name: str, args: dict, exid: int) -> dict:
    """Validate and run one tool call. Unknown tools and bad arguments are errors returned to the model."""
    if name not in TOOLS: return {"error": f"unknown tool '{name}'"}
    _, model, _, fn = TOOLS[name]
    try:
        parsed = model.model_validate(args or {})
    except ValidationError as e:
        return {"error": f"invalid arguments: {e.errors()[:3]}"}
    try:
        return fn(s, uid, parsed, exid)
    except (LookupError, PermissionError, ValueError) as e:
        s.rollback()   # a failed call must not leave half its changes behind
        return {"error": str(e)}
