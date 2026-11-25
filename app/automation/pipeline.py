"""One application from Queued to a final state: preflight, prepare, fill, submit, verify, snapshot.
The user's Apply click is the authorization; anything they have not approved pauses the application instead."""
import time
import uuid
from datetime import timedelta
from sqlalchemy import select

from .. import answers, config, db, events, letters, profile as profiles, resumes, site_sessions
from ..ai.provider import LLMError
from ..platforms import PLATFORMS, adapters
from . import states as S
from .applications import adapter_for, snapshot, submitted_today
from .flows import Navigator
from .forms import FormContext
from ..platforms.generic import NeedsYou

WORKER_ID = uuid.uuid4().hex
DELAY = {"wellfound": 45, "internshala": 60, "linkedin": 90, "other": 30, "freehire": 30, "testboard": 0}   # seconds between applications; provisional, human-paced
LOGIN_NEEDED = {"wellfound", "internshala", "linkedin"}


def _lock(s, a: db.Application) -> bool:
    exp = a.lock_expires.replace(tzinfo=db.now().tzinfo) if a.lock_expires and not a.lock_expires.tzinfo else a.lock_expires
    if a.lock_owner and a.lock_owner != WORKER_ID and exp and exp > db.now(): return False
    a.lock_owner, a.lock_expires = WORKER_ID, db.now() + timedelta(minutes=15); s.commit(); return True


def _unlock(s, a):
    a.lock_owner, a.lock_expires = "", None; s.commit()


def _ai_answer(s, user_id, p, job):
    """Profile-grounded answers for non-sensitive questions; None unless the model is confident."""
    from pydantic import BaseModel
    from ..ai import service

    class A(BaseModel):
        answer: str | None
        confident: bool

    def ask(q, options):
        if answers.is_sensitive(q): return None
        try:
            r = service.structured(s, user_id, "form answer",
                                   "Answer one job-application question for the candidate, only if the profile states it or it follows "
                                   "directly. Opinions, stories, tests or facts not in the profile: answer null. Multiple choice: one option "
                                   "exactly as written. Under 60 words, first person, no em-dashes.",
                                   f"PROFILE:\n{p.model_dump_json(exclude={'personal': {'address'}})[:7000]}\nJOB: {job.title} at {job.company}\n"
                                   f"QUESTION: {service.untrusted(q, 500)}\nOPTIONS: {options or 'free text'}", A)
        except LLMError:
            return None
        return r.answer if r.confident and r.answer and (not options or r.answer in options) else None
    return ask


def run_one(s, user_id: int, a: db.Application, browser, execution_id: int) -> str:
    job = s.get(db.Job, a.job_id)
    emit = lambda msg, **kw: events.emit(s, user_id, msg, execution_id=execution_id, platform=job.platform, **kw)
    if not _lock(s, a):
        emit(f"{job.title}: another run holds this application; skipped", level="warn", operation="lock"); return a.state
    try:
        # ---- preflight
        S.move(a, S.PREFLIGHT); s.commit(); emit(f"Preflight: {job.title} at {job.company}", operation="preflight")
        twin = s.scalar(select(db.Application).join(db.Job, db.Job.id == db.Application.job_id).where(
            db.Application.user_id == user_id, db.Application.id != a.id, db.Application.state == S.SUBMITTED,
            db.Job.raw["dedup"].as_string() == (job.raw or {}).get("dedup", "")))
        if twin:
            S.move(a, S.DUPLICATE, blocker=f"You already applied to {job.company}: {job.title}"); emit("Duplicate: already submitted", level="warn", status="completed"); return a.state
        p, _ = profiles.current(s, user_id)
        pct, missing = profiles.completeness(p)
        if not (p.personal.name and p.personal.email and p.personal.phone):
            S.move(a, S.AWAITING, blocker="Add your name, email and phone to your profile"); emit(a.blocker, level="wait", status="waiting"); return a.state
        t = resumes.tailored_for(s, user_id, job.id)
        r = s.get(db.Resume, a.resume_id) if a.resume_id else t if t and t.approved else resumes.default(s, user_id)
        if not r or not r.approved:
            S.move(a, S.AWAITING, blocker="Upload a resume and make it your default"); emit(a.blocker, level="wait", status="waiting"); return a.state
        a.resume_id = r.id
        if submitted_today(s, user_id) >= (s.get(db.UserSettings, user_id) or db.UserSettings(daily_limit=15)).daily_limit:
            S.move(a, S.SELECTED, blocker="Daily application limit reached; it stays selected for tomorrow"); emit(a.blocker, level="warn", status="completed"); return a.state
        adapter = adapter_for(s, user_id, job)
        try:
            checker = adapters()[0].get(job.platform)   # the platform knows whether the posting is open, not the form system
            available = checker.check_availability(browser, job) if checker else None
        except Exception as e:
            available = None; emit(f"Could not check whether the posting is open ({e}); continuing", level="warn")
        if available is False:
            job.active = False; S.move(a, S.BLOCKED, blocker="The posting is closed"); emit("Posting closed", level="warn", status="completed"); return a.state
        if adapter and job.platform in LOGIN_NEEDED and job.platform not in site_sessions.status(s, user_id):   # only when the app submits
            S.move(a, S.AWAITING, blocker=f"Sign in to {PLATFORMS[job.platform].name} on the Settings page, then retry"); emit(a.blocker, level="wait", status="waiting"); return a.state

        # ---- prepare
        S.move(a, S.PREPARING); s.commit()
        _, v = letters.current(s, user_id, job.id)
        if not v:
            try: v = letters.generate(s, user_id, job, (s.get(db.UserSettings, user_id) or db.UserSettings()).letter_style)
            except LLMError as e: emit(f"No cover letter ({e}); applying without one", level="warn")
        if v and v.issues: emit(f"Cover letter has {len(v.issues)} evidence warning(s): {'; '.join(v.issues[:2])}", level="warn")
        a.cover_letter_version_id = v.id if v else None
        cover_pdf = str(letters.export_pdf(s, user_id, v)) if v else None
        if adapter is None:
            pol = PLATFORMS.get(job.platform, PLATFORMS["other"])
            S.move(a, S.AWAITING, blocker=f"Assisted ({pol.terms_note}). Open the job, submit it with the prepared resume and cover letter, then press 'I submitted it'.")
            emit(f"Prepared for you to submit on {pol.name}", level="wait", status="waiting", operation="prepare"); return a.state
        opted = (s.get(db.UserSettings, user_id) or db.UserSettings(opt_in=[])).opt_in or []
        ctx = FormContext(profile=p, rules=answers.rules(s, user_id), resume_path=str(resumes.path(r)),
                          cover_text=letters.text_of(v, p.personal.name) if v else "", cover_path=cover_pdf,
                          ai_answer=_ai_answer(s, user_id, p, job), navigator=Navigator(s, user_id), use_google="google" in opted)

        # ---- fill
        S.move(a, S.FILLING); s.commit(); emit("Opening the application form", operation="fill")
        try:
            res = adapter.fill_application(browser, job, ctx)
        except NeedsYou as e:
            browser.window("normal"); S.move(a, S.AWAITING, blocker=str(e)); emit(a.blocker, level="wait", status="waiting"); return a.state
        barrier = browser.barrier()
        if barrier:
            browser.window("normal")
            S.move(a, S.AWAITING, blocker=f"The site wants you to prove you're human ({barrier}). Complete it in the app's browser window, then press Retry.")
            emit(a.blocker, level="wait", status="waiting"); return a.state
        s.query(db.ApplicationAnswer).filter_by(application_id=a.id).delete()
        for x in res.answers: s.add(db.ApplicationAnswer(application_id=a.id, **x))
        if res.missing:
            S.move(a, S.AWAITING, pending=res.missing, blocker=f"{len(res.missing)} question(s) need your answer")
            emit(f"{len(res.missing)} question(s) need your answer: {'; '.join(m['question'][:60] for m in res.missing[:3])}", level="wait", status="waiting"); return a.state
        if not res.resume_uploaded:
            S.move(a, S.AWAITING, blocker="The form had no resume upload the app recognised; check it in the browser and retry")
            emit(a.blocker, level="wait", status="waiting"); return a.state
        S.move(a, S.READY); s.commit(); emit(f"Form filled ({len(res.answers)} fields)", operation="fill", level="success", status="completed")

        # ---- submit (authorized by your Apply click)
        S.move(a, S.SUBMITTING); a.attempts += 1; s.commit(); emit("Submitting", operation="submit")
        try:
            text, url = adapter.submit_application(browser, job)
        except NeedsYou as e:   # raised before anything was clicked
            S.move(a, S.FAILED, error=str(e), blocker=str(e)); emit(str(e), level="error", status="failed"); return a.state
        except Exception as e:
            S.move(a, S.UNCONFIRMED, error=f"{type(e).__name__}: {e}"[:500], blocker="The submit step failed part-way. Check the site before retrying.")
            emit(f"Submission outcome unknown: {e}", level="error", status="failed"); return a.state
        verdict = adapter.verify_submission(job, text, url)
        shot = config.user_dir(user_id, "evidence", str(a.id)) / "confirmation.png"
        try: browser.screenshot(str(shot))
        except Exception: shot = None
        a.evidence = {"kind": "page", "detail": verdict.detail, "url": url, "screenshot": shot.name if shot else ""}
        if verdict.confirmed:
            S.move(a, S.SUBMITTED, submitted_at=db.now(), reference=verdict.reference)
            snapshot(s, a, answered=res.answers)
            emit(f"Submitted: {verdict.detail}", level="success", status="completed", operation="verify")
        elif browser.barrier():
            browser.window("normal")
            S.move(a, S.UNCONFIRMED, blocker=f"After Submit the site asked you to prove you're human ({browser.barrier()}). Complete it in the app's browser window, "
                                            "then press 'I submitted it' once the site confirms.")
        else:
            S.move(a, S.UNCONFIRMED, blocker="No confirmation was found. Check the site; press 'I submitted it' only if it went through.")
            emit(f"Submission unconfirmed: {verdict.detail}", level="warn", status="completed", operation="verify")
        return a.state
    except S.TransitionError:
        raise
    except Exception as e:
        if a.state == S.SUBMITTING:
            S.move(a, S.UNCONFIRMED, error=str(e)[:500])
        elif a.state in (S.PREFLIGHT, S.PREPARING, S.FILLING, S.READY):
            S.move(a, S.FAILED, error=f"{type(e).__name__}: {e}"[:500])
        emit(f"{job.title}: {type(e).__name__}: {e}", level="error", status="failed")
        return a.state
    finally:
        s.commit(); _unlock(s, a)


def pause_between(platform: str, cancelled) -> None:
    end = time.monotonic() + DELAY.get(platform, 20)
    while time.monotonic() < end and not cancelled(): time.sleep(0.5)
