"""Auto-apply: with your standing permission, the app searches, ranks and applies on a schedule without asking each
time. It only applies where the app may submit, to jobs at or above your minimum score, within your daily limit.
CAPTCHAs, sensitive questions without a saved answer, and questions it can't answer from your profile still wait for you."""
from datetime import datetime, timedelta
import httpx
from sqlalchemy import select

from .. import db, events, jobs, profile as profiles
from ..ai import keys, service
from ..ai.provider import LLMError
from . import applications, states as S

EVERY_HOURS = (6, 12, 24)
RANK_PER_RUN = 30   # provisional: keeps one run's Groq use small on the free tier; raise if your limits allow


def is_due(cfg: dict, now: datetime) -> bool:
    if not cfg.get("on"): return False
    last = cfg.get("last_run")
    return not last or now - datetime.fromisoformat(last) >= timedelta(hours=int(cfg.get("every_hours", 12)))


def schedule(s) -> list[int]:
    """Queue a run for everyone whose Auto-apply is due and not already running. Returns their user ids."""
    from . import worker
    now, started = db.now(), []
    for st in s.scalars(select(db.UserSettings)):
        busy = s.scalar(select(db.Execution.id).where(db.Execution.user_id == st.user_id, db.Execution.kind == "autopilot",
                                                      db.Execution.status.in_(["queued", "running"])))
        if busy or not is_due(st.autopilot or {}, now): continue
        st.autopilot = {**st.autopilot, "last_run": now.isoformat()}
        worker.enqueue(s, st.user_id, "autopilot", {}); started.append(st.user_id)
    s.commit()
    return started


def sources(s, user_id: int) -> list[str]:
    return ["freehire"] + (["web"] if keys.get(s, user_id, "brave") else [])


def candidates(s, user_id: int, min_score: int) -> list[db.Job]:
    """Jobs it may apply to now: ranked at or above your minimum, fit your rules, not applied to, and automatable."""
    out = []
    for j in s.scalars(select(db.Job).where(db.Job.user_id == user_id, db.Job.active.is_(True))):
        sc = jobs.latest_score(s, user_id, j.id)
        if not sc or sc.score < min_score or j.gates.get("verdict") == "fail" or (j.raw or {}).get("duplicate_of"): continue
        if applications.for_job(s, user_id, j.id) or applications.adapter_for(s, user_id, j) is None: continue
        out.append((sc.score, j))
    return [j for _, j in sorted(out, key=lambda t: -t[0])]


def run(s, ex, cancelled) -> str:
    from .worker import browser_for
    uid, st = ex.user_id, service.settings(s, ex.user_id)
    p, _ = profiles.current(s, uid)
    roles, place = p.preferences.roles[:2], (p.preferences.locations or [""])[0]
    if not roles: raise ValueError("Add the roles you want on My profile; Auto-apply searches for them")
    emit = lambda msg, **kw: events.emit(s, uid, msg, execution_id=ex.id, operation="autopilot", **kw)
    emit(f"Auto-apply: searching for {', '.join(roles)}" + (f" in {place}" if place else ""))
    found = []
    for role in roles:
        for src in sources(s, uid):
            if cancelled(): return "stopped"
            try:
                found += jobs.search(s, uid, src, {"query": role, "location": place, "days": 7}, browser=browser_for(uid) if src == "web" else None,
                                     execution_id=ex.id)
            except (PermissionError, RuntimeError, httpx.HTTPError) as e:
                emit(f"Search on {src} failed: {e}", level="warn")
    ranked = 0
    for j in {j.id: j for j in found}.values():
        if ranked >= RANK_PER_RUN or cancelled(): break
        if jobs.latest_score(s, uid, j.id): continue
        try:
            jobs.rank(s, uid, j, execution_id=ex.id); s.commit(); ranked += 1
        except LLMError as e:
            emit(f"Ranking stopped: {e}", level="warn"); break
    room = st.daily_limit - applications.submitted_today(s, uid) - len(list(s.scalars(select(db.Application.id).where(
        db.Application.user_id == uid, db.Application.state.in_([S.QUEUED, S.PREFLIGHT, S.PREPARING, S.FILLING, S.READY, S.SUBMITTING])))))
    picks = candidates(s, uid, st.min_score)[:max(room, 0)]
    if not picks:
        emit(f"Auto-apply: {len(found)} found, {ranked} ranked; nothing new scores {st.min_score}+ or today's limit is reached", level="success", status="completed")
        return f"{len(found)} found, nothing to apply to"
    apps = applications.select_jobs(s, uid, [j.id for j in picks])
    applications.authorize(s, uid, [a.id for a in apps])
    emit(f"Auto-apply: applying to {len(apps)} job(s) scoring {st.min_score}+ under your standing permission", level="success", status="completed")
    return f"{len(found)} found, {ranked} ranked, applying to {len(apps)}"
