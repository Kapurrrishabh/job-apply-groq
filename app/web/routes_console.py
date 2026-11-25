"""Dashboard, AI Execution Console (real events), Agent, and Agent Skills."""
from fastapi import Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from .. import db, events, jobs
from ..agent import skills, tools
from ..ai import service
from ..automation import applications, states as S, worker
from .core import CTX, Ctx, app, back, page


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, ctx: Ctx = CTX):
    top = []
    for j in ctx.s.scalars(select(db.Job).where(db.Job.user_id == ctx.uid, db.Job.active.is_(True)).order_by(db.Job.id.desc()).limit(300)):
        sc = jobs.latest_score(ctx.s, ctx.uid, j.id)
        if sc and j.gates.get("verdict") != "fail": top.append((sc.score, j, sc))
    top.sort(key=lambda t: -t[0])
    apps = lambda states, n: list(ctx.s.execute(select(db.Application, db.Job).join(db.Job, db.Job.id == db.Application.job_id).where(
        db.Application.user_id == ctx.uid, db.Application.state.in_(states)).order_by(db.Application.updated_at.desc()).limit(n)).all())
    return page(request, "dashboard.html", ctx, top=[(j, sc, applications.for_job(ctx.s, ctx.uid, j.id)) for _, j, sc in top[:5]],
                attention=apps([S.AWAITING, S.UNCONFIRMED], 5), sent=apps([S.SUBMITTED], 5), recent=events.since(ctx.s, ctx.uid, 0, 8)[::-1],
                auto=service.settings(ctx.s, ctx.uid).autopilot or {})


@app.get("/console", response_class=HTMLResponse)
def console(request: Request, ctx: Ctx = CTX):
    runs = list(ctx.s.scalars(select(db.Execution).where(db.Execution.user_id == ctx.uid).order_by(db.Execution.id.desc()).limit(15)))
    return page(request, "console.html", ctx, runs=runs)


@app.get("/api/events")
def api_events(after: int = 0, ctx: Ctx = CTX):
    evs = events.since(ctx.s, ctx.uid, after, 300)
    runs = ctx.s.scalars(select(db.Execution).where(db.Execution.user_id == ctx.uid, db.Execution.status.in_(["queued", "running"])))
    return {"events": [{"id": e.id, "ts": e.ts.strftime("%H:%M:%S"), "level": e.level, "op": e.operation, "skill": e.skill, "platform": e.platform,
                        "status": e.status, "message": e.message, "duration_ms": e.duration_ms, "detail": e.detail, "run": e.execution_id} for e in evs],
            "active": [{"id": r.id, "kind": r.kind, "status": r.status} for r in runs]}


@app.post("/console/clear")
def clear(ctx: Ctx = CTX):
    ctx.s.query(db.ExecutionEvent).filter_by(user_id=ctx.uid).delete()
    return back("/console", "Console cleared")


@app.post("/executions/{eid}/cancel")
def cancel(eid: int, ctx: Ctx = CTX):
    try: worker.cancel(ctx.s, ctx.uid, eid)
    except LookupError as e: return back("/console", str(e), "err")
    return back("/console", "Stopping after the current step")


@app.get("/agent", response_class=HTMLResponse)
def agent_page(request: Request, ctx: Ctx = CTX):
    runs = list(ctx.s.scalars(select(db.Execution).where(db.Execution.user_id == ctx.uid, db.Execution.kind == "agent").order_by(db.Execution.id.desc()).limit(10)))
    return page(request, "agent.html", ctx, runs=runs)


@app.post("/agent")
def agent_run(prompt: str = Form(...), ctx: Ctx = CTX):
    if not prompt.strip(): return back("/agent", "Type what you want the agent to do", "err")
    worker.enqueue(ctx.s, ctx.uid, "agent", {"prompt": prompt.strip()[:2000]})
    return back("/console", "The agent is working; its tool calls appear here")


@app.get("/skills", response_class=HTMLResponse)
def skills_page(request: Request, ctx: Ctx = CTX):
    return page(request, "skills.html", ctx, skills=skills.registry(), prov=skills.provenance(),
                tools=[(n, d, perm) for n, (d, _, perm, _) in tools.TOOLS.items()])
