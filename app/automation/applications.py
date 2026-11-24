"""Application lifecycle actions the user takes, plus snapshots (what was actually submitted, encrypted)."""
import shutil
from sqlalchemy import func, select
from .. import answers, config, crypto, db, events, letters, profile as profiles, resumes
from . import states as S


def get(s, user_id: int, app_id: int) -> db.Application:
    a = s.scalar(select(db.Application).where(db.Application.id == app_id, db.Application.user_id == user_id))
    if not a: raise LookupError("Application not found")
    return a


def for_job(s, user_id: int, job_id: int) -> db.Application | None:
    return s.scalar(select(db.Application).where(db.Application.user_id == user_id, db.Application.job_id == job_id))


def select_jobs(s, user_id: int, job_ids: list[int]) -> list[db.Application]:
    from .. import jobs
    out = []
    for jid in job_ids:
        job = jobs.get(s, user_id, jid)
        a = for_job(s, user_id, job.id)
        if a is None:
            score = jobs.latest_score(s, user_id, job.id)
            a = db.Application(user_id=user_id, job_id=job.id, platform=job.platform, state=S.SELECTED, score_at_apply=score.score if score else None)
            s.add(a)
        elif a.state in (S.RANKED, S.DISCOVERED, S.FAILED, S.BLOCKED, S.QUEUED, S.UNCONFIRMED):
            S.move(a, S.SELECTED, error="", blocker="")
        out.append(a)
    s.flush(); return out


def unselect(s, user_id: int, app_id: int) -> None:
    a = get(s, user_id, app_id)
    if a.state != S.SELECTED: raise S.TransitionError("Only selected applications can be removed from the queue")
    s.delete(a)


def authorize(s, user_id: int, app_ids: list[int]):
    """Your Apply click: queue these applications for submission in one execution."""
    from . import worker
    ids = []
    for aid in app_ids:
        a = get(s, user_id, aid)
        if a.state in (S.SELECTED, S.FAILED): S.move(a, S.QUEUED, error="", blocker=""); ids.append(a.id)
    if not ids: raise ValueError("Nothing to apply to: select jobs first")
    ex = worker.enqueue(s, user_id, "apply_batch", {"application_ids": ids})
    events.emit(s, user_id, f"You authorized {len(ids)} application(s)", operation="authorize", execution_id=ex.id, level="info", status="completed")
    return ex


def answer_pending(s, user_id: int, app_id: int, replies: dict[int, str]) -> None:
    from . import worker
    a = get(s, user_id, app_id)
    if a.state != S.AWAITING: raise S.TransitionError("This application is not waiting for answers")
    for i, q in enumerate(a.pending or []):
        r = (replies.get(i) or "").strip()
        if r: answers.remember(s, user_id, q["question"], r)
    a.pending = []
    S.move(a, S.QUEUED, blocker="")
    worker.enqueue(s, user_id, "apply_batch", {"application_ids": [a.id]})


def retry(s, user_id: int, app_id: int) -> None:
    from . import worker
    a = get(s, user_id, app_id)
    if a.state not in (S.AWAITING, S.FAILED): raise S.TransitionError(f"'{a.state}' applications cannot be retried")
    S.move(a, S.QUEUED, blocker="", error="")
    worker.enqueue(s, user_id, "apply_batch", {"application_ids": [a.id]})


def confirm_submitted(s, user_id: int, app_id: int, note: str = "") -> db.Application:
    """Assisted platforms and unconfirmed submissions: you checked the site and it was sent."""
    a = get(s, user_id, app_id)
    S.move(a, S.SUBMITTED, submitted_at=db.now(), blocker="",
           evidence={**(a.evidence or {}), "kind": "user_confirmed", "detail": note.strip()[:500] or "You confirmed you submitted it on the platform"})
    snapshot(s, a, answered=[])
    return a


def snapshot(s, a: db.Application, answered: list[dict]) -> None:
    """Freeze exactly what was sent. Later profile edits never change it."""
    job = s.get(db.Job, a.job_id); p, version = profiles.current(s, a.user_id)
    r = s.get(db.Resume, a.resume_id) if a.resume_id else None
    v = s.get(db.CoverLetterVersion, a.cover_letter_version_id) if a.cover_letter_version_id else None
    resume_copy = ""
    if r:
        dest = config.user_dir(a.user_id, "submitted", str(a.id)); src = resumes.path(r)
        if src.exists(): shutil.copy2(src, dest / src.name); resume_copy = src.name
    data = {"job": {"title": job.title, "company": job.company, "platform": job.platform, "url": job.url, "location": job.location},
            "candidate": p.personal.model_dump(exclude={"address"}), "profile_version": version,
            "resume": {"label": r.label, "sha256": r.sha256, "file": resume_copy} if r else None,
            "cover_letter": letters.text_of(v, p.personal.name) if v else "",
            "answers": answered or [{"question": x.question, "answer": x.answer, "source": x.source} for x in
                                    s.scalars(select(db.ApplicationAnswer).where(db.ApplicationAnswer.application_id == a.id))],
            "submitted_at": a.submitted_at.isoformat() if a.submitted_at else "", "evidence": a.evidence, "reference": a.reference,
            "score": a.score_at_apply}
    row = s.scalar(select(db.ApplicationSnapshot).where(db.ApplicationSnapshot.application_id == a.id))
    if row: row.ciphertext = crypto.encrypt_json(data)
    else: s.add(db.ApplicationSnapshot(application_id=a.id, ciphertext=crypto.encrypt_json(data)))
    s.flush()


def preview(s, user_id: int, app_id: int) -> dict | None:
    a = get(s, user_id, app_id)
    row = s.scalar(select(db.ApplicationSnapshot).where(db.ApplicationSnapshot.application_id == a.id))
    return crypto.decrypt_json(row.ciphertext) if row else None


def delete(s, user_id: int, app_id: int) -> None:
    a = get(s, user_id, app_id)
    shutil.rmtree(config.instance() / "users" / str(user_id) / "submitted" / str(a.id), ignore_errors=True)
    shutil.rmtree(config.instance() / "users" / str(user_id) / "evidence" / str(a.id), ignore_errors=True)
    s.delete(a)


def submitted_today(s, user_id: int) -> int:
    today = db.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return s.scalar(select(func.count(db.Application.id)).where(db.Application.user_id == user_id, db.Application.state == S.SUBMITTED,
                                                                 db.Application.submitted_at >= today)) or 0
