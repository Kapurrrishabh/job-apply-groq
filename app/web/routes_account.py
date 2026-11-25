"""Sign-up, sign-in, settings (Groq key, model, limits, ranking weights), legacy import, diagnostics."""
from fastapi import Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select, text

from .. import auth, config, db, events, legacy, site_sessions
from ..ai import keys, service
from ..ai.provider import MODELS, LLMError
from ..ranking import DEFAULT_WEIGHTS
from .core import CTX, Ctx, app, back, page


def _cookie(resp, token: str):
    resp.set_cookie(auth.COOKIE, token, httponly=True, samesite="strict", max_age=config.SESSION_DAYS * 86400, path="/")
    return resp


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return page(request, "register.html")


@app.post("/register")
def register(request: Request, email: str = Form(...), name: str = Form(""), password: str = Form(...)):
    s = db.session()
    try:
        u = auth.register(s, email, name, password); token = auth.start_session(s, u); s.commit()
    except auth.AuthError as e:
        s.rollback(); return back("/register", str(e), "err")
    finally:
        s.close()
    return _cookie(RedirectResponse("/?msg=Welcome!+Start+with+your+profile.", status_code=303), token)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    s = db.session(); first = not s.scalar(select(func.count(db.User.id))); s.close()
    return RedirectResponse("/register", status_code=303) if first else page(request, "login.html")


@app.post("/login")
def login(email: str = Form(...), password: str = Form(...), next: str = Form("/")):
    s = db.session()
    try:
        u = auth.authenticate(s, email, password); token = auth.start_session(s, u); s.commit()
    except auth.AuthError as e:
        s.rollback(); return back("/login", str(e), "err")
    finally:
        s.close()
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    return _cookie(RedirectResponse(target, status_code=303), token)


@app.post("/logout")
def logout(request: Request, ctx: Ctx = CTX):
    auth.end_session(ctx.s, request.cookies.get(auth.COOKIE))
    r = RedirectResponse("/login", status_code=303); r.delete_cookie(auth.COOKIE, path="/"); return r


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, ctx: Ctx = CTX):
    from ..platforms import PLATFORMS
    st = service.settings(ctx.s, ctx.uid)
    usage = ctx.s.execute(select(func.count(db.LlmUsage.id), func.coalesce(func.sum(db.LlmUsage.prompt_tokens + db.LlmUsage.completion_tokens), 0))
                          .where(db.LlmUsage.user_id == ctx.uid)).one()
    return page(request, "settings.html", ctx, key_hint=keys.hint(ctx.s, ctx.uid), brave_hint=keys.hint(ctx.s, ctx.uid, "brave"), st=st, models=MODELS,
                weights={**DEFAULT_WEIGHTS, **(st.weights or {})}, usage=usage, platforms=PLATFORMS,
                logins=site_sessions.status(ctx.s, ctx.uid), legacy=legacy.available(), opted=st.opt_in or [])


@app.post("/settings")
async def save_settings(request: Request, ctx: Ctx = CTX):
    f = await request.form(); st = service.settings(ctx.s, ctx.uid)
    if f.get("api_key", "").strip():
        try: keys.store(ctx.s, ctx.uid, f["api_key"])
        except ValueError as e: return back("/settings", str(e), "err")
    if f.get("model") not in MODELS: return back("/settings", "Unknown model", "err")
    try:
        st.model, st.temperature = f["model"], min(max(float(f.get("temperature", 0.2)), 0.0), 1.0)
        st.max_tokens, st.min_score = min(max(int(f.get("max_tokens", config.DEFAULT_MAX_TOKENS)), 256), 8000), min(max(int(f.get("min_score", 60)), 0), 100)
        st.daily_limit, st.letter_style = min(max(int(f.get("daily_limit", 15)), 1), 50), f.get("letter_style", "professional")
        st.weights = {k: max(0.0, float(f.get(f"w_{k}", v))) for k, v in DEFAULT_WEIGHTS.items()}
    except ValueError:
        return back("/settings", "Settings must be numbers where numbers are asked for", "err")
    if not sum(st.weights.values()): return back("/settings", "At least one ranking weight must be above zero", "err")
    if ctx.s.get(db.UserSettings, ctx.uid) is None: ctx.s.add(st)
    return back("/settings", "Settings saved")


@app.post("/settings/brave")
def save_brave(brave_key: str = Form(""), ctx: Ctx = CTX):
    try: keys.store(ctx.s, ctx.uid, brave_key, "brave")
    except ValueError as e: return back("/settings", f"Brave key: {e}", "err")
    return back("/settings", "Brave Search key saved; choose 'Web search' as the source on Find jobs")


@app.post("/settings/autopilot")
def save_autopilot(on: str = Form(""), every_hours: int = Form(12), consent: str = Form(""), ctx: Ctx = CTX):
    from ..automation.autopilot import EVERY_HOURS
    if every_hours not in EVERY_HOURS: return back("/settings#autopilot", "Choose how often Auto-apply runs", "err")
    if on and consent != "yes": return back("/settings#autopilot", "Tick the standing-permission box to turn on Auto-apply", "err")
    st = service.settings(ctx.s, ctx.uid)
    cfg = {**(st.autopilot or {}), "on": bool(on), "every_hours": every_hours}
    if on: cfg["accepted_at"] = cfg.get("accepted_at") or db.now().isoformat()
    st.autopilot = cfg
    if ctx.s.get(db.UserSettings, ctx.uid) is None: ctx.s.add(st)
    events.emit(ctx.s, ctx.uid, f"Auto-apply switched {'on, every ' + str(every_hours) + ' hours' if on else 'off'} by you", operation="settings", level="warn")
    return back("/settings#autopilot", "Auto-apply is on; the first run starts within a minute" if on else "Auto-apply is off")


@app.post("/settings/autopilot/run")
def run_autopilot(ctx: Ctx = CTX):
    from ..automation import worker
    st = service.settings(ctx.s, ctx.uid)
    if not (st.autopilot or {}).get("on"): return back("/settings#autopilot", "Turn on Auto-apply first", "err")
    st.autopilot = {**st.autopilot, "last_run": db.now().isoformat()}
    worker.enqueue(ctx.s, ctx.uid, "autopilot", {})
    return back("/console", "Auto-apply is running now; follow it here")


@app.post("/settings/test")
def test_groq(ctx: Ctx = CTX):
    try:
        with events.step(ctx.s, ctx.uid, "groq.test", "Testing Groq connection", skill="ai"):
            model = service.test_connection(ctx.s, ctx.uid)
        return back("/settings", f"Groq connected ({model})")
    except LLMError as e:
        return back("/settings", str(e), "err")


@app.post("/settings/key/delete")
def delete_key(provider: str = Form("groq"), ctx: Ctx = CTX):
    if provider not in ("groq", "brave"): return back("/settings", "Unknown key", "err")
    keys.delete(ctx.s, ctx.uid, provider); return back("/settings", f"{provider.title()} key removed")


@app.post("/settings/import-legacy")
def import_legacy(ctx: Ctx = CTX):
    if not legacy.available(): return back("/settings", "No earlier data to import: none was found, or another account already imported it", "err")
    done = ctx.s.scalar(select(func.count(db.ProfileVersion.id)).where(db.ProfileVersion.user_id == ctx.uid))
    if done: return back("/settings", "Your account already has a profile; the earlier data was not imported again", "err")
    with events.step(ctx.s, ctx.uid, "legacy.import", "Importing data from the earlier version"):
        counts = legacy.run(ctx.s, ctx.uid, committed_profile=config.ROOT / "instance" / "legacy-profile-before-import.yaml")
    return back("/settings", "Imported: " + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in counts.items()))


@app.get("/health")
def health():
    s = db.session()
    try:
        s.execute(text("SELECT 1")); return {"status": "ok"}
    finally:
        s.close()


@app.get("/diagnostics", response_class=HTMLResponse)
def diagnostics(request: Request, ctx: Ctx = CTX):
    from ..agent import skills
    from ..automation import worker
    import shutil
    checks = [("Database", True, f"{config.db_path()}"),
              ("Groq key", keys.hint(ctx.s, ctx.uid) != "not set", keys.hint(ctx.s, ctx.uid)),
              ("Browser worker", worker.alive(), "running" if worker.alive() else "stopped"),
              ("Google Chrome or Chromium", bool(shutil.which("google-chrome") or shutil.which("chromium") or (config.Path("/Applications/Google Chrome.app").exists())), "used for applications"),
              ("TeX (PDF CVs)", bool(shutil.which("lualatex") or config.Path("/Library/TeX/texbin/lualatex").exists()), "optional; plain PDFs are used without it"),
              ("Agent skills", bool(skills.registry()), f"{len(skills.registry())} skills loaded")]
    for p, row in site_sessions.status(ctx.s, ctx.uid).items():
        checks.append((f"{p} login", True, f"saved until {row.expires_at:%b %d %H:%M} UTC"))
    return page(request, "diagnostics.html", ctx, checks=checks)


@app.post("/sites/{platform}/{action}")
def site_login(platform: str, action: str, accept: str = Form(""), ctx: Ctx = CTX):
    from ..automation import worker
    from ..platforms import PLATFORMS
    pol = PLATFORMS.get(platform)
    if not pol or not pol.login_url or action not in ("login", "check_login", "forget", "automate_on", "automate_off"):
        return back("/settings#sites", "Unknown site or action", "err")
    if action in ("automate_on", "automate_off"):
        if not pol.opt_in: return back("/settings#sites", f"{pol.name} can't be switched", "err")
        st = service.settings(ctx.s, ctx.uid); chosen = set(st.opt_in or [])
        if action == "automate_on" and accept != "yes": return back("/settings#sites", "Tick the box to accept the risk first", "err")
        st.opt_in = sorted(chosen | {platform} if action == "automate_on" else chosen - {platform})
        if ctx.s.get(db.UserSettings, ctx.uid) is None: ctx.s.add(st)
        events.emit(ctx.s, ctx.uid, f"{pol.name} automation switched {'on' if action == 'automate_on' else 'off'} by you", operation="settings", level="warn")
        return back("/settings#sites", f"{pol.name}: the app will {'now apply for you' if action == 'automate_on' else 'only prepare; you submit'}")
    if action == "forget":
        site_sessions.forget(ctx.s, ctx.uid, platform); return back("/settings#sites", f"{pol.name} login forgotten")
    worker.enqueue(ctx.s, ctx.uid, action, {"platform": platform})
    return back("/console", f"Opening {pol.name} in the app's browser" if action == "login" else f"Checking your {pol.name} login")
