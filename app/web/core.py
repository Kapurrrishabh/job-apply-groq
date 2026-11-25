"""FastAPI app, templates, request guards (local-only, signed-in user, CSRF) and small helpers."""
import hashlib
import re
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from .. import auth, config, db
from ..automation.states import LABELS as STATE_LABELS

HERE = Path(__file__).resolve().parent.parent
LOCAL_HOSTS = {"127.0.0.1", "localhost", "testserver"}
PUBLIC_PATHS = ("/login", "/register", "/static/", "/health")


@asynccontextmanager
async def lifespan(_app):
    db.init()
    from ..automation import worker
    if config.setting("JOBAPPLY_NO_WORKER") != "1":   # tests drive the queue themselves
        worker.start()
    yield


app = FastAPI(title="job-apply-groq", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


def safe_url(url) -> str:
    """Only http(s) links reach an href; anything else (javascript:, data:) becomes '#'."""
    u = str(url or "")
    return u if urlparse(u).scheme in ("http", "https") else "#"


templates.env.filters["safe_url"] = safe_url
templates.env.filters["state_label"] = lambda state: STATE_LABELS.get(state, state)
templates.env.filters["initials"] = lambda name: ("".join(w[0] for w in re.findall(r"[A-Za-z0-9]\w*", str(name or ""))[:2]) or "?").upper()
templates.env.filters["hue"] = lambda name: int(hashlib.sha256(str(name or "").encode()).hexdigest()[:4], 16) % 360   # a steady colour per company


class Ctx:
    """Per-request database session, signed-in user and their auth session."""
    def __init__(self, s, user: db.User, sess: db.AuthSession):
        self.s, self.user, self.sess = s, user, sess

    @property
    def uid(self) -> int:
        return self.user.id


@app.middleware("http")
async def local_only(request: Request, call_next):
    host = (request.headers.get("host") or "").split(":")[0]
    origin = request.headers.get("origin") or request.headers.get("referer")
    if host not in LOCAL_HOSTS or (origin and urlparse(origin).hostname not in LOCAL_HOSTS):
        return JSONResponse({"error": "this app only accepts requests from its own pages on this machine"}, status_code=403)
    resp = await call_next(request)
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'; object-src 'none'")
    return resp


class LoginRequired(Exception):
    pass


@app.exception_handler(LoginRequired)
async def _to_login(request: Request, _exc):
    return RedirectResponse(f"/login?next={quote(request.url.path)}", status_code=303)


@app.exception_handler(LookupError)
async def _not_found(request: Request, exc: LookupError):
    return HTMLResponse(f'<p>{escape(str(exc))}. <a href="/">Back to the dashboard</a></p>', status_code=404)


async def user_ctx(request: Request):
    s = db.session()
    try:
        a = auth.session_for(s, request.cookies.get(auth.COOKIE))
        if not a:
            s.commit(); raise LoginRequired()
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            submitted = request.headers.get("x-csrf-token")
            if not submitted and "form" in (request.headers.get("content-type") or ""):
                submitted = (await request.form()).get("csrf")
            if not auth.csrf_ok(a, submitted): raise HTTPException(403, "Form expired or forged (CSRF check failed). Reload the page and try again.")
        yield Ctx(s, s.get(db.User, a.user_id), a)
        s.commit()
    except Exception:
        s.rollback(); raise
    finally:
        s.close()


CTX = Depends(user_ctx)


def page(request: Request, name: str, ctx: Ctx | None = None, **data):
    data.update(request=request, nav=name.rsplit(".", 1)[0], user=ctx.user if ctx else None,
                csrf=ctx.sess.csrf if ctx else "", msg=request.query_params.get("msg"), kind=request.query_params.get("kind", "ok"))
    if ctx:
        data["journey"] = journey(ctx.s, ctx.uid)
        data.setdefault("has_key", data["journey"]["has_key"])
        data.setdefault("waiting", data["journey"]["waiting"])
    return templates.TemplateResponse(request, name, data)


def journey(s, uid: int) -> dict:
    """Where this person is on the way from setup to tracked applications. Every page shows it."""
    from sqlalchemy import func, select
    from .. import profile as profiles, resumes
    from ..ai import keys
    from ..automation import states as S
    pct, missing = profiles.completeness(profiles.current(s, uid)[0])
    count = lambda *st: s.scalar(select(func.count(db.Application.id)).where(db.Application.user_id == uid, db.Application.state.in_(st))) or 0
    has_key, has_resume = keys.hint(s, uid) != "not set", resumes.default(s, uid) is not None
    found = s.scalar(select(func.count(db.Job.id)).where(db.Job.user_id == uid)) or 0
    short, waiting, sent = count(S.SELECTED, S.QUEUED, S.FAILED), count(S.AWAITING, S.UNCONFIRMED), count(S.SUBMITTED)
    ready = has_key and has_resume and pct >= 80
    todo = "add your Groq key" if not has_key else "upload your resume" if not has_resume else f"profile {pct}% complete"
    steps = [{"title": "Get ready", "detail": "all set" if ready else todo, "done": ready,
              "href": "/settings" if not has_key else "/resumes" if not has_resume else "/profile"},
             {"title": "Find jobs", "detail": f"{found} found", "done": found > 0, "href": "/jobs"},
             {"title": "Shortlist", "detail": f"{short} shortlisted", "done": short > 0 or sent > 0, "href": "/queue" if short else "/jobs"},
             {"title": "Apply", "detail": f"{waiting} need you" if waiting else "you press Apply", "done": sent > 0 and not waiting and not short,
              "href": "/queue"},
             {"title": "Track", "detail": f"{sent} sent", "done": sent > 0, "href": "/applications"}]
    current = next((i for i, st in enumerate(steps) if not st["done"]), len(steps) - 1)
    return {"steps": steps, "current": current, "pct": pct, "missing": missing, "has_key": has_key, "has_resume": has_resume,
            "found": found, "shortlisted": short, "waiting": waiting, "sent": sent}


def back(url: str, msg: str = "", kind: str = "ok") -> RedirectResponse:
    path, hash_, anchor = url.partition("#")
    if msg:
        path += ("&" if "?" in path else "?") + f"msg={quote(msg)}&kind={kind}"
    return RedirectResponse(path + hash_ + anchor, status_code=303)


def csrf_field(token: str) -> Markup:
    return Markup(f'<input type="hidden" name="csrf" value="{escape(token)}">')


templates.env.globals["csrf_field"] = csrf_field


def platform_name(key: str) -> str:
    from ..platforms import PLATFORMS
    return PLATFORMS[key].name if key in PLATFORMS else "Earlier version" if key == "imported" else key


templates.env.globals["platform_name"] = platform_name
