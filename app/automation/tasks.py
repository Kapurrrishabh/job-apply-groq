"""What each execution kind does. Each handler gets (session, execution, cancelled()) and returns a summary."""
import re
from .. import events, jobs, letters, site_sessions
from ..platforms import PLATFORMS, adapters
from . import states as S
from .applications import get, release
from .pipeline import pause_between, run_one
from .worker import browser_for


def apply_batch(s, ex, cancelled):
    ids, done = ex.params["application_ids"], {}
    events.emit(s, ex.user_id, f"Applying to {len(ids)} job(s), one at a time", execution_id=ex.id, operation="apply")
    try:
        for n, aid in enumerate(ids):
            if cancelled():
                events.emit(s, ex.user_id, "Stopped by you; the rest are back on your shortlist", execution_id=ex.id, level="warn", status="paused"); break
            a = get(s, ex.user_id, aid)
            if a.state != S.QUEUED: continue
            b = browser_for(ex.user_id)
            state = run_one(s, ex.user_id, a, b, ex.id)
            done[state] = done.get(state, 0) + 1
            if n < len(ids) - 1: pause_between(a.platform, cancelled)
    except Exception:
        s.rollback(); raise
    finally:
        release(s, ex.user_id, ids); s.commit()
    return ", ".join(f"{v} {k}" for k, v in done.items()) or "nothing to do"


def search(s, ex, cancelled):
    p = dict(ex.params); platform = p.pop("platform")
    browser = browser_for(ex.user_id) if platform == "web" else None   # web results on Internshala or LinkedIn are read in the app's browser
    return f"{len(jobs.search(s, ex.user_id, platform, p, browser=browser, execution_id=ex.id))} jobs"


def rank(s, ex, cancelled):
    n = 0
    for jid in ex.params["job_ids"]:
        if cancelled(): break
        job = jobs.get(s, ex.user_id, jid)
        sc = jobs.rank(s, ex.user_id, job, execution_id=ex.id); s.commit(); n += 1
        events.emit(s, ex.user_id, f"{job.title} at {job.company}: {sc.score}/100 ({sc.recommendation}, {sc.confidence} confidence)",
                    execution_id=ex.id, operation="rank", skill="job-application-assistant", level="success", status="completed")
    return f"{n} ranked"


def letter(s, ex, cancelled):
    job = jobs.get(s, ex.user_id, ex.params["job_id"])
    v = letters.generate(s, ex.user_id, job, ex.params.get("style", "professional"))
    return f"version {v.number}" + (f", {len(v.issues)} warning(s)" if v.issues else "")


def tailor(s, ex, cancelled):
    from ..documents import tailor as build
    r = build(s, ex.user_id, jobs.get(s, ex.user_id, ex.params["job_id"]))
    events.emit(s, ex.user_id, f"Tailored CV ready for your review: {r.label}", execution_id=ex.id, operation="tailor", level="success",
                status="completed", detail={"resume_id": r.id})
    return f"resume {r.id}"


def agent(s, ex, cancelled):
    from ..agent.orchestrator import run
    return run(s, ex.user_id, ex.params["prompt"], ex.id, cancelled)


def login(s, ex, cancelled):
    pol = PLATFORMS[ex.params["platform"]]
    b = browser_for(ex.user_id); b.goto(pol.login_url); b.window("normal")
    events.emit(s, ex.user_id, f"Opened {pol.name} sign-in. Sign in in the browser window (solve any CAPTCHA yourself), then press Check.",
                execution_id=ex.id, platform=ex.params["platform"], level="wait", status="waiting", operation="login")
    return "opened sign-in page"


def check_login(s, ex, cancelled):
    platform = ex.params["platform"]; pol = PLATFORMS[platform]
    b = browser_for(ex.user_id); b.goto(pol.check_url or f"https://{pol.domain}/"); b.wait(3000)
    ok = not re.search(r"(?i)login|signin|servicelogin", b.url()) and any(c.get("domain", "").lstrip(".").endswith(pol.domain) for c in b.cookies())
    if ok:
        row = site_sessions.save(s, ex.user_id, platform, b.cookies(), domain=pol.domain); b.window("minimized")
        events.emit(s, ex.user_id, f"{pol.name}: signed in; login saved until {row.expires_at:%b %d %H:%M} UTC", execution_id=ex.id,
                    platform=platform, level="success", status="completed", operation="login")
        return "signed in"
    events.emit(s, ex.user_id, f"{pol.name}: not signed in yet", execution_id=ex.id, platform=platform, level="warn", status="completed", operation="login")
    return "not signed in"


def import_page(s, ex, cancelled):
    b, platform = browser_for(ex.user_id), ex.params["platform"]
    row, new = jobs.upsert(s, ex.user_id, adapters()[0][platform].import_current_page(b))
    events.emit(s, ex.user_id, f"Imported {row.title} at {row.company} from {PLATFORMS[platform].name}", execution_id=ex.id, platform=platform,
                level="success", status="completed", operation="import", detail={"job_id": row.id})
    return f"job {row.id}"


def open_url(s, ex, cancelled):
    b = browser_for(ex.user_id); b.goto(ex.params["url"]); b.window("normal")
    return "opened"


def autopilot(s, ex, cancelled):
    from .autopilot import run
    return run(s, ex, cancelled)


HANDLERS = {"apply_batch": apply_batch, "autopilot": autopilot, "search": search, "rank": rank, "letter": letter, "tailor": tailor, "agent": agent, "login": login,
            "check_login": check_login, "import_page": import_page, "open_url": open_url}
