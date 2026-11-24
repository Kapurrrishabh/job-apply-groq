"""My Profile, Resume Manager (with merge-review import) and saved Answers."""
import yaml
from fastapi import File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import ValidationError
from sqlalchemy import func, select

from .. import answers, config, crypto, db, events, profile as profiles, resumes
from ..ai import service
from ..ai.provider import LLMError
from .core import CTX, Ctx, app, back, page


def _proposal_file(uid: int):
    return config.user_dir(uid) / "proposal.bin"


@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, ctx: Ctx = CTX):
    p, v = profiles.current(ctx.s, ctx.uid)
    pct, missing = profiles.completeness(p)
    text = yaml.safe_dump(p.model_dump(exclude={"personal", "preferences"}), sort_keys=False, allow_unicode=True, width=120)
    return page(request, "profile.html", ctx, p=p, version=v, pct=pct, missing=missing, text=text, versions=profiles.versions(ctx.s, ctx.uid)[:12])


@app.post("/profile/basic")
async def save_basic(request: Request, ctx: Ctx = CTX):
    f = await request.form(); p, _ = profiles.current(ctx.s, ctx.uid)
    lst = lambda k: [x.strip() for x in (f.get(k) or "").split(",") if x.strip()]
    try:
        for k in ("name", "email", "phone", "current_location", "address", "citizenship", "linkedin", "github", "portfolio", "headline"):
            setattr(p.personal, k, (f.get(k) or "").strip())
        pr = p.preferences
        pr.roles, pr.industries, pr.locations = lst("roles"), lst("industries"), lst("locations")
        pr.work_modes = [m for m in f.getlist("work_modes") if m in ("remote", "hybrid", "onsite")]
        pr.job_types = [t for t in f.getlist("job_types") if t in ("internship", "full-time", "part-time", "contract")]
        pr.expected_salary, pr.notice_period, pr.availability = f.get("expected_salary", ""), f.get("notice_period", ""), f.get("availability", "")
        pr.salary_floor_lpa = {"software": float(f.get("floor_sw") or 0), "other": float(f.get("floor_other") or 0)}
        pr.max_years_required, pr.company_size_min = int(f.get("max_years") or 1), int(f.get("size_min") or 1)
        pr.service_firms = lst("service_firms")
        profiles.Profile.model_validate(p.model_dump())
    except (ValueError, ValidationError) as e:
        return back("/profile", f"Not saved: {e}", "err")
    v = profiles.save(ctx.s, ctx.uid, p, "manual", "Edited basics and preferences")
    return back("/profile", f"Saved as version {v}")


@app.post("/profile/sections")
def save_sections(text: str = Form(...), ctx: Ctx = CTX):
    p, _ = profiles.current(ctx.s, ctx.uid)
    try:
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict): raise ValueError("expected sections like 'education:' and 'experience:'")
        new = profiles.Profile.model_validate({**p.model_dump(), **{k: v for k, v in data.items() if k not in ("personal", "preferences")}})
    except (ValueError, ValidationError, yaml.YAMLError) as e:
        return back("/profile#sections", f"Not saved: {str(e)[:300]}", "err")
    v = profiles.save(ctx.s, ctx.uid, new, "manual", "Edited sections")
    return back("/profile#sections", f"Saved as version {v}")


@app.post("/profile/restore/{version}")
def restore(version: int, ctx: Ctx = CTX):
    row = ctx.s.scalar(select(db.ProfileVersion).where(db.ProfileVersion.user_id == ctx.uid, db.ProfileVersion.version == version))
    if not row: return back("/profile", "No such version", "err")
    v = profiles.save(ctx.s, ctx.uid, profiles.Profile.model_validate(row.data), "manual", f"Restored version {version}")
    return back("/profile", f"Version {version} restored as version {v}")


# ---------- resumes ----------

@app.get("/resumes", response_class=HTMLResponse)
def resumes_page(request: Request, ctx: Ctx = CTX):
    return page(request, "resumes.html", ctx, items=resumes.listing(ctx.s, ctx.uid))


@app.post("/resumes/upload")
async def upload(file: UploadFile = File(...), label: str = Form(""), ctx: Ctx = CTX):
    data = await file.read(config.MAX_UPLOAD_BYTES + 1)
    try:
        r = resumes.add(ctx.s, ctx.uid, file.filename or "", data, label)
    except resumes.UploadError as e:
        return back("/resumes", str(e), "err")
    events.emit(ctx.s, ctx.uid, f"Resume uploaded: {r.label} ({len(r.text)} characters of text)", operation="resume", level="success", status="completed")
    return back("/resumes", f"Uploaded {r.label}. Press 'Update profile from this resume' to import it.")


@app.get("/resumes/{rid}/file")
def resume_file(rid: int, download: int = 0, ctx: Ctx = CTX):
    r = resumes.get(ctx.s, ctx.uid, rid)
    return FileResponse(resumes.path(r), media_type=r.mime, filename=f"{r.label}{resumes.path(r).suffix}",
                        content_disposition_type="attachment" if download or r.mime != "application/pdf" else "inline")


@app.get("/resumes/{rid}", response_class=HTMLResponse)
def resume_view(request: Request, rid: int, ctx: Ctx = CTX):
    return page(request, "resume.html", ctx, r=resumes.get(ctx.s, ctx.uid, rid))


@app.post("/resumes/{rid}/default")
def make_default(rid: int, ctx: Ctx = CTX):
    try: resumes.set_default(ctx.s, ctx.uid, rid)
    except resumes.UploadError as e: return back("/resumes", str(e), "err")
    return back("/resumes", "Default resume set")


@app.post("/resumes/{rid}/approve")
def approve(rid: int, ctx: Ctx = CTX):
    resumes.get(ctx.s, ctx.uid, rid).approved = True
    return back(f"/resumes/{rid}", "Approved: it can now be used in applications")


@app.post("/resumes/{rid}/delete")
def delete_resume(rid: int, ctx: Ctx = CTX):
    used = ctx.s.scalar(select(func.count(db.Application.id)).where(db.Application.user_id == ctx.uid, db.Application.resume_id == rid))
    resumes.delete(ctx.s, ctx.uid, rid)
    return back("/resumes", "Deleted" + (f"; {used} application record(s) keep their own saved copy" if used else ""))


@app.post("/resumes/{rid}/import")
def import_resume(rid: int, ctx: Ctx = CTX):
    r = resumes.get(ctx.s, ctx.uid, rid)
    system = ("Extract the candidate's details from the resume text into the JSON structure. Copy facts exactly as written. "
              "Give every experience and project a short id and every bullet an id. Leave fields empty when the resume does not say. "
              "Do not invent, summarise, upgrade or reword anything.")
    try:
        with events.step(ctx.s, ctx.uid, "resume.parse", f"Reading {r.label} with Groq", skill="resume"):
            parsed = service.structured(ctx.s, ctx.uid, "resume parsing", system, service.untrusted(r.text, 30000), profiles.Facts)
    except LLMError as e:
        return back("/resumes", str(e), "err")
    r.parsed = parsed.model_dump()
    current, _ = profiles.current(ctx.s, ctx.uid)
    proposed, changes = profiles.merge(current, parsed, f"resume:{r.id}")
    _proposal_file(ctx.uid).write_bytes(crypto.encrypt_json({"resume_id": r.id, "proposed": proposed.model_dump(), "changes": changes}))
    return back("/profile/review", f"{len(changes)} change(s) found. Review them before anything is saved.")


@app.get("/profile/review", response_class=HTMLResponse)
def review(request: Request, ctx: Ctx = CTX):
    f = _proposal_file(ctx.uid)
    if not f.exists(): return back("/profile", "Nothing to review")
    data = crypto.decrypt_json(f.read_bytes())
    return page(request, "profile_review.html", ctx, changes=data["changes"], resume_id=data["resume_id"])


@app.post("/profile/review")
async def apply_review(request: Request, ctx: Ctx = CTX):
    form = await request.form(); f = _proposal_file(ctx.uid)
    if form.get("action") == "discard" or not f.exists():
        f.unlink(missing_ok=True); return back("/profile", "Import discarded; your profile is unchanged")
    data = crypto.decrypt_json(f.read_bytes())
    accepted = {int(x) for x in form.getlist("take")}
    final = profiles.apply_conflicts(profiles.Profile.model_validate(data["proposed"]), data["changes"], accepted)
    v = profiles.save(ctx.s, ctx.uid, final, "import", f"Imported from resume {data['resume_id']}")
    f.unlink(missing_ok=True)
    return back("/profile", f"Imported into version {v}. Your earlier version is kept in the history.")


# ---------- answers ----------

@app.get("/answers", response_class=HTMLResponse)
def answers_page(request: Request, test: str = "", ctx: Ctx = CTX):
    rules = answers.rules(ctx.s, ctx.uid)
    return page(request, "answers.html", ctx, rules=rules, test=test, test_answer=answers.lookup(rules, test) if test else None,
                test_sensitive=answers.is_sensitive(test) if test else False)


@app.post("/answers")
async def save_answers(request: Request, ctx: Ctx = CTX):
    import re
    f = await request.form()
    rows = []
    for i in range(int(f.get("count", 0)) + 1):
        pat, ans = (f.get(f"pattern_{i}") or "").strip(), (f.get(f"answer_{i}") or "").strip()
        if not pat or not ans or f.get(f"delete_{i}"): continue
        try: re.compile(pat)
        except re.error as e: return back("/answers", f"'{pat}' is not a valid pattern: {e}", "err")
        rows.append(db.AnswerRule(user_id=ctx.uid, position=i, pattern=pat, answer=ans, note=(f.get(f"note_{i}") or "").strip(),
                                  sensitive=answers.is_sensitive(pat)))
    ctx.s.query(db.AnswerRule).filter_by(user_id=ctx.uid).delete()
    ctx.s.add_all(rows)
    return back("/answers", f"Saved {len(rows)} answers")
