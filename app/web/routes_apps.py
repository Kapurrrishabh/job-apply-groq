"""Application Queue, My Applications (with the saved submission preview), and Cover Letter Studio."""
from pathlib import Path
from fastapi import Form, Request
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import or_, select

from .. import config, db, jobs, letters, resumes
from ..automation import applications, states as S, worker
from ..platforms import PLATFORMS, apply_adapter
from .core import CTX, Ctx, app, back, page

QUEUE_STATES = [S.SELECTED, S.QUEUED, S.PREFLIGHT, S.PREPARING, S.FILLING, S.AWAITING, S.READY, S.SUBMITTING, S.FAILED, S.UNCONFIRMED, S.BLOCKED]
HISTORY_STATES = [S.SUBMITTED, S.UNCONFIRMED, S.FAILED, S.BLOCKED, S.DUPLICATE]


def _rows(s, uid, states_, q=""):
    stmt = select(db.Application, db.Job).join(db.Job, db.Job.id == db.Application.job_id).where(db.Application.user_id == uid, db.Application.state.in_(states_))
    if q: stmt = stmt.where(or_(db.Job.title.ilike(f"%{q}%"), db.Job.company.ilike(f"%{q}%")))
    return list(s.execute(stmt.order_by(db.Application.updated_at.desc())).all())


@app.get("/queue", response_class=HTMLResponse)
def queue(request: Request, ctx: Ctx = CTX):
    rows = _rows(ctx.s, ctx.uid, QUEUE_STATES)
    info = {a.id: (PLATFORMS.get(j.platform, PLATFORMS["other"]), apply_adapter(j) is not None, letters.current(ctx.s, ctx.uid, j.id)[1]) for a, j in rows}
    runs = list(ctx.s.scalars(select(db.Execution).where(db.Execution.user_id == ctx.uid, db.Execution.kind == "apply_batch",
                                                         db.Execution.status.in_(["queued", "running"]))))
    return page(request, "queue.html", ctx, rows=rows, info=info, resume=resumes.default(ctx.s, ctx.uid), runs=runs,
                limit=(ctx.s.get(db.UserSettings, ctx.uid) or db.UserSettings()).daily_limit, today=applications.submitted_today(ctx.s, ctx.uid))


@app.post("/queue/apply")
async def apply_selected(request: Request, ctx: Ctx = CTX):
    f = await request.form(); ids = [int(x) for x in f.getlist("app") if x.isdigit()]
    if f.get("confirm") != "yes": return back("/queue", "Tick the confirmation box to authorize submission", "err")
    try:
        ex = applications.authorize(ctx.s, ctx.uid, ids)
    except (ValueError, LookupError, S.TransitionError) as e:
        return back("/queue", str(e), "err")
    return back("/console", f"Applying to {len(ex.params['application_ids'])} job(s). Follow it here.")


@app.post("/queue/{aid}/remove")
def remove(aid: int, ctx: Ctx = CTX):
    try: applications.unselect(ctx.s, ctx.uid, aid)
    except S.TransitionError as e: return back("/queue", str(e), "err")
    return back("/queue", "Removed from the queue")


@app.post("/applications/{aid}/answer")
async def answer(aid: int, request: Request, ctx: Ctx = CTX):
    f = await request.form()
    try:
        applications.answer_pending(ctx.s, ctx.uid, aid, {i: f.get(f"a_{i}", "") for i in range(50)})
    except (LookupError, S.TransitionError) as e:
        return back("/queue", str(e), "err")
    return back("/console", "Answers saved for next time; the application is queued again")


@app.post("/applications/{aid}/retry")
def retry(aid: int, ctx: Ctx = CTX):
    try: applications.retry(ctx.s, ctx.uid, aid)
    except (LookupError, S.TransitionError) as e: return back("/queue", str(e), "err")
    return back("/console", "Queued again")


@app.post("/applications/{aid}/confirm")
def confirm(aid: int, note: str = Form(""), ctx: Ctx = CTX):
    try: applications.confirm_submitted(ctx.s, ctx.uid, aid, note)
    except (LookupError, S.TransitionError) as e: return back("/queue", str(e), "err")
    return back(f"/applications/{aid}", "Recorded as submitted (confirmed by you)")


@app.post("/applications/{aid}/delete")
def delete(aid: int, ctx: Ctx = CTX):
    applications.delete(ctx.s, ctx.uid, aid)
    return back("/applications", "Application record and its saved files deleted")


@app.get("/applications", response_class=HTMLResponse)
def history(request: Request, q: str = "", state: str = "", ctx: Ctx = CTX):
    rows = _rows(ctx.s, ctx.uid, [state] if state in HISTORY_STATES else HISTORY_STATES, q)
    return page(request, "applications.html", ctx, rows=rows, q=q, state=state, states=HISTORY_STATES)


@app.get("/applications/{aid}", response_class=HTMLResponse)
def application(request: Request, aid: int, ctx: Ctx = CTX):
    a = applications.get(ctx.s, ctx.uid, aid)
    job = ctx.s.get(db.Job, a.job_id)
    answers = list(ctx.s.scalars(select(db.ApplicationAnswer).where(db.ApplicationAnswer.application_id == a.id)))
    return page(request, "application.html", ctx, a=a, j=job, snap=applications.preview(ctx.s, ctx.uid, aid), answers=answers,
                shot=(a.evidence or {}).get("screenshot"))


@app.get("/applications/{aid}/evidence.png")
def evidence(aid: int, ctx: Ctx = CTX):
    a = applications.get(ctx.s, ctx.uid, aid)
    name = Path((a.evidence or {}).get("screenshot") or "").name
    f = config.instance() / "users" / str(ctx.uid) / "evidence" / str(a.id) / name
    if not name or not f.exists(): return back(f"/applications/{aid}", "No screenshot saved", "err")
    return FileResponse(f, media_type="image/png")


@app.get("/applications/{aid}/resume")
def submitted_resume(aid: int, ctx: Ctx = CTX):
    snap = applications.preview(ctx.s, ctx.uid, aid) or {}
    name = Path(((snap.get("resume") or {}).get("file")) or "").name
    f = config.instance() / "users" / str(ctx.uid) / "submitted" / str(aid) / name
    if not name or not f.exists(): return back(f"/applications/{aid}", "The submitted resume copy is not stored", "err")
    return FileResponse(f, filename=name)


# ---------- Cover Letter Studio ----------

@app.get("/letters", response_class=HTMLResponse)
def letters_index(request: Request, ctx: Ctx = CTX):
    rows = list(ctx.s.execute(select(db.CoverLetter, db.Job).join(db.Job, db.Job.id == db.CoverLetter.job_id)
                              .where(db.CoverLetter.user_id == ctx.uid).order_by(db.CoverLetter.id.desc())).all())
    return page(request, "letters.html", ctx, rows=rows)


@app.get("/letters/{jid}", response_class=HTMLResponse)
def studio(request: Request, jid: int, ctx: Ctx = CTX):
    job = jobs.get(ctx.s, ctx.uid, jid)
    cl, v = letters.current(ctx.s, ctx.uid, jid)
    return page(request, "studio.html", ctx, j=job, cl=cl, v=v, versions=letters.history(ctx.s, cl.id) if cl else [], styles=letters.STYLES)


@app.post("/letters/{jid}/generate")
def generate(jid: int, style: str = Form("professional"), ctx: Ctx = CTX):
    jobs.get(ctx.s, ctx.uid, jid)
    if style not in letters.STYLES: return back(f"/letters/{jid}", "Unknown style", "err")
    worker.enqueue(ctx.s, ctx.uid, "letter", {"job_id": jid, "style": style})
    return back(f"/letters/{jid}", "Writing… refresh in a few seconds")


@app.post("/letters/{jid}/save")
async def save_letter(jid: int, request: Request, ctx: Ctx = CTX):
    f = await request.form(); job = jobs.get(ctx.s, ctx.uid, jid)
    paras = [f.get(f"p_{i}", "") for i in range(int(f.get("count", 0)))]
    v = letters.edit(ctx.s, ctx.uid, job, paras)
    return back(f"/letters/{jid}", f"Saved version {v.number}" + (f" with {len(v.issues)} warning(s)" if v.issues else ""))


@app.post("/letters/{jid}/regenerate")
def regenerate(jid: int, index: int = Form(...), instruction: str = Form(""), ctx: Ctx = CTX):
    from ..ai.provider import LLMError
    try:
        v = letters.regenerate_paragraph(ctx.s, ctx.uid, jobs.get(ctx.s, ctx.uid, jid), index, instruction)
    except (LLMError, LookupError) as e:
        return back(f"/letters/{jid}", str(e), "err")
    return back(f"/letters/{jid}", f"Paragraph {index + 1} rewritten (version {v.number})")


@app.get("/letters/{jid}/pdf")
def letter_pdf(jid: int, ctx: Ctx = CTX):
    _, v = letters.current(ctx.s, ctx.uid, jid)
    if not v: return back(f"/letters/{jid}", "Write a letter first", "err")
    return FileResponse(letters.export_pdf(ctx.s, ctx.uid, v), media_type="application/pdf")
