"""Job Search, Ranked Jobs, Job Details, saved searches and imports."""
import httpx
from fastapi import Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import or_, select

from .. import db, jobs, letters
from ..ai import service
from ..automation import applications, worker
from ..platforms import PLATFORMS, offered
from .core import CTX, Ctx, app, back, page

PAGE = 25


@app.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request, q: str = "", platform: str = "", verdict: str = "", min_score: int = 0, mode: str = "",
              jtype: str = "", sort: str = "score", bookmarked: int = 0, pg: int = 1, ctx: Ctx = CTX):
    stmt = select(db.Job).where(db.Job.user_id == ctx.uid)
    if q: stmt = stmt.where(or_(db.Job.title.ilike(f"%{q}%"), db.Job.company.ilike(f"%{q}%"), db.Job.description.ilike(f"%{q}%")))
    if platform: stmt = stmt.where(db.Job.platform == platform)
    if mode: stmt = stmt.where(db.Job.work_mode == mode)
    if jtype: stmt = stmt.where(db.Job.employment_type == jtype)
    if bookmarked: stmt = stmt.where(db.Job.bookmarked.is_(True))
    rows = list(ctx.s.scalars(stmt.order_by(db.Job.id.desc()).limit(1000)))
    items = []
    for j in rows:
        if verdict and j.gates.get("verdict") != verdict: continue
        sc = jobs.latest_score(ctx.s, ctx.uid, j.id)
        if min_score and (not sc or sc.score < min_score): continue
        items.append((j, sc, applications.for_job(ctx.s, ctx.uid, j.id)))
    key = {"score": lambda t: -(t[1].score if t[1] else -1), "newest": lambda t: -(t[0].posted_at.timestamp() if t[0].posted_at else 0),
           "company": lambda t: t[0].company.lower()}.get(sort, lambda t: 0)
    items.sort(key=key)
    total = len(items); items = items[(pg - 1) * PAGE: pg * PAGE]
    history = list(ctx.s.scalars(select(db.SavedSearch).where(db.SavedSearch.user_id == ctx.uid).order_by(db.SavedSearch.id.desc()).limit(8)))
    saved = list(ctx.s.scalars(select(db.SavedSearch).where(db.SavedSearch.user_id == ctx.uid, db.SavedSearch.saved.is_(True)).order_by(db.SavedSearch.name)))
    from .. import profile as profiles
    p, _ = profiles.current(ctx.s, ctx.uid)
    return page(request, "jobs.html", ctx, items=items, total=total, pg=pg, pages=max(1, -(-total // PAGE)), platforms=offered(),
                f=dict(q=q, platform=platform, verdict=verdict, min_score=min_score, mode=mode, jtype=jtype, sort=sort, bookmarked=bookmarked),
                history=history, saved=saved, roles=p.preferences.roles, locations=p.preferences.locations,
                opted=service.settings(ctx.s, ctx.uid).opt_in or [])


@app.post("/jobs/search")
def start_search(platform: str = Form("freehire"), query: str = Form(...), location: str = Form("India"), days: int = Form(14),
                 save: str = Form(""), ctx: Ctx = CTX):
    if platform not in offered() or PLATFORMS[platform].discovery not in ("api", "test"):
        return back("/jobs", f"{PLATFORMS.get(platform, PLATFORMS['other']).name} jobs are imported, not searched", "err")
    qs = [x.strip() for x in query.split(",") if x.strip()][:5]
    for qy in qs:
        params = {"platform": platform, "query": qy, "location": location.strip(), "days": max(1, min(days, 60))}
        worker.enqueue(ctx.s, ctx.uid, "search", params)
        if save: ctx.s.add(db.SavedSearch(user_id=ctx.uid, name=f"{qy} @ {location}", platform=platform, params=params, saved=True))
    return back("/console", f"Searching {len(qs)} quer{'y' if len(qs) == 1 else 'ies'}; results appear on Job Search")


@app.post("/searches/{sid}/run")
def run_saved(sid: int, ctx: Ctx = CTX):
    ss = ctx.s.scalar(select(db.SavedSearch).where(db.SavedSearch.id == sid, db.SavedSearch.user_id == ctx.uid))
    if not ss: return back("/jobs", "Search not found", "err")
    worker.enqueue(ctx.s, ctx.uid, "search", ss.params); ss.last_run_at = db.now()
    return back("/console", f"Running '{ss.name}'")


@app.post("/searches/{sid}/delete")
def delete_saved(sid: int, ctx: Ctx = CTX):
    ss = ctx.s.scalar(select(db.SavedSearch).where(db.SavedSearch.id == sid, db.SavedSearch.user_id == ctx.uid))
    if ss: ctx.s.delete(ss)
    return back("/jobs", "Removed")


@app.post("/jobs/import")
def import_job(url: str = Form(""), title: str = Form(""), company: str = Form(""), location: str = Form(""), salary: str = Form(""),
               description: str = Form(""), ctx: Ctx = CTX):
    try:
        job = jobs.import_manual(ctx.s, ctx.uid, {"url": url, "title": title, "company": company, "location": location, "salary": salary,
                                                 "description": description})
    except ValueError as e:
        return back("/jobs", str(e), "err")
    except httpx.HTTPError as e:
        return back("/jobs", f"Couldn't read the job from the employer's site ({type(e).__name__}); try again, or add the title and description", "err")
    return back(f"/jobs/{job.id}", "Imported. Rank it to see how well it fits.")


@app.post("/jobs/import-page")
def import_page(platform: str = Form(...), ctx: Ctx = CTX):
    pol = PLATFORMS.get(platform, PLATFORMS["other"])
    if pol.discovery != "page_import" and not (pol.opt_in and platform in (service.settings(ctx.s, ctx.uid).opt_in or [])):
        return back("/jobs", "That site's jobs can't be imported from the browser", "err")
    worker.enqueue(ctx.s, ctx.uid, "import_page", {"platform": platform})
    return back("/console", f"Reading the {PLATFORMS[platform].name} job open in the app's browser")


@app.post("/jobs/bulk")
async def bulk(request: Request, ctx: Ctx = CTX):
    f = await request.form(); ids = [int(x) for x in f.getlist("job") if x.isdigit()]
    if not ids: return back("/jobs", "Select at least one job", "err")
    action = f.get("action")
    for jid in ids: jobs.get(ctx.s, ctx.uid, jid)   # ownership check before acting
    if action == "rank":
        worker.enqueue(ctx.s, ctx.uid, "rank", {"job_ids": ids}); return back("/console", f"Ranking {len(ids)} job(s)")
    if action == "select":
        applications.select_jobs(ctx.s, ctx.uid, ids); return back("/queue", f"{len(ids)} job(s) added to your application queue")
    if action == "bookmark":
        for jid in ids: jobs.get(ctx.s, ctx.uid, jid).bookmarked = True
        return back("/jobs", "Bookmarked")
    return back("/jobs", "Unknown action", "err")


@app.get("/jobs/{jid}", response_class=HTMLResponse)
def job_page(request: Request, jid: int, ctx: Ctx = CTX):
    from .. import resumes
    job = jobs.get(ctx.s, ctx.uid, jid)
    sc = jobs.latest_score(ctx.s, ctx.uid, jid)
    cl, v = letters.current(ctx.s, ctx.uid, jid)
    a = applications.for_job(ctx.s, ctx.uid, jid)
    pol = PLATFORMS.get(job.platform, PLATFORMS["other"])
    return page(request, "job.html", ctx, j=job, sc=sc, letter=v, app_row=a, pol=pol, automated=applications.adapter_for(ctx.s, ctx.uid, job) is not None,
                resume=resumes.default(ctx.s, ctx.uid), tailored=resumes.tailored_for(ctx.s, ctx.uid, jid))


@app.post("/jobs/{jid}/rank")
def rank_one(jid: int, ctx: Ctx = CTX):
    jobs.get(ctx.s, ctx.uid, jid); worker.enqueue(ctx.s, ctx.uid, "rank", {"job_ids": [jid]})
    return back(f"/jobs/{jid}", "Ranking queued; refresh in a few seconds")



@app.post("/jobs/{jid}/tailor")
def tailor(jid: int, ctx: Ctx = CTX):
    jobs.get(ctx.s, ctx.uid, jid); worker.enqueue(ctx.s, ctx.uid, "tailor", {"job_id": jid})
    return back("/console", "Tailoring your CV; it will wait under Resumes for your approval")


@app.post("/jobs/{jid}/bookmark")
def bookmark(jid: int, ctx: Ctx = CTX):
    j = jobs.get(ctx.s, ctx.uid, jid); j.bookmarked = not j.bookmarked
    return back(f"/jobs/{jid}", "Bookmarked" if j.bookmarked else "Bookmark removed")


@app.post("/jobs/{jid}/open")
def open_in_browser(jid: int, ctx: Ctx = CTX):
    from .core import safe_url
    j = jobs.get(ctx.s, ctx.uid, jid); url = safe_url(j.apply_url or j.url)
    if url == "#": return back(f"/jobs/{jid}", "This job has no web address to open", "err")
    worker.enqueue(ctx.s, ctx.uid, "open_url", {"url": url})
    return back(f"/jobs/{jid}", "Opened in the app's browser window")
