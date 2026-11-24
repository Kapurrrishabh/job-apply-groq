"""Cover Letter Studio: Groq drafts per job, evidence check against the profile, versions, paragraph regeneration,
PDF export (fpdf2, so no TeX is needed)."""
import json
import re
from pathlib import Path
from pydantic import BaseModel
from sqlalchemy import func, select

from . import config, db, events, profile as profiles
from .agent import skills

STYLES = {
    "professional": "Professional and warm, 250-300 words.",
    "concise": "Short and direct, 150-180 words.",
    "technical": "Technical: lead with systems built, tools and measurable results, 250-300 words.",
    "internship": "For an internship: eagerness to learn backed by concrete projects and the internship window, 200-250 words.",
    "startup": "For a startup: ownership, speed and building end to end, 200-260 words.",
    "experienced": "For an experienced candidate: scope, impact and leadership, 280-320 words.",
}
HYPE = re.compile(r"(?i)\b(passionate|thrilled|synergy|rockstar|ninja|guru|world-class|cutting-edge|leverage my)\b")


class Draft(BaseModel):
    paragraphs: list[str]


class OneParagraph(BaseModel):
    paragraph: str


def numbers(text: str) -> set[str]:
    return {m.group(0).rstrip("+").lower() for m in re.finditer(r"\d+(?:[.,]\d+)?[kKmM%]?\+?", text or "")}


def check(paragraphs: list[str], profile: profiles.Profile, job: db.Job) -> list[str]:
    """Evidence problems: numbers that are in neither the profile nor the posting, hype words, em-dashes."""
    allowed = numbers(profile.model_dump_json()) | numbers(f"{job.title} {job.description}")
    text = "\n".join(paragraphs)
    issues = [f"'{n}' is not in your profile or the posting" for n in sorted(numbers(text) - allowed)]
    issues += [f"hype word '{m.group(0)}'" for m in HYPE.finditer(text)]
    if "—" in text: issues.append("contains an em-dash")
    return issues


def _letter(s, user_id: int, job: db.Job, style: str) -> db.CoverLetter:
    cl = s.scalar(select(db.CoverLetter).where(db.CoverLetter.user_id == user_id, db.CoverLetter.job_id == job.id))
    if not cl: cl = db.CoverLetter(user_id=user_id, job_id=job.id, style=style); s.add(cl); s.flush()
    cl.style = style
    return cl


def _save(s, cl: db.CoverLetter, paragraphs: list[str], source: str, issues: list[str]) -> db.CoverLetterVersion:
    n = (s.scalar(select(func.max(db.CoverLetterVersion.number)).where(db.CoverLetterVersion.cover_letter_id == cl.id)) or 0) + 1
    v = db.CoverLetterVersion(cover_letter_id=cl.id, number=n, paragraphs=[p.strip() for p in paragraphs if p.strip()], source=source, issues=issues)
    s.add(v); s.flush(); return v


def _system(style: str) -> str:
    return ("You write a cover letter for the candidate, as plain paragraphs (no greeting line, no sign-off; the app adds them). "
            f"Style: {STYLES.get(style, STYLES['professional'])} Open with the role and the candidate's current status. Second paragraph: what "
            "the company or role does, using only what the posting says (never invent company research, connections or facts). Then "
            "2-3 concrete achievements from the profile that match the posting, with their real numbers. State the most relevant gap "
            "once, plainly, if there is one. End with how the candidate works and their availability. Natural first-person English, "
            "short sentences, no em-dashes, no hype words. Every claim must come from the candidate profile.\n"
            f"Writing guidance from the job-application-assistant skill:\n{skills.guidance('job-application-assistant', 3000)}")


def generate(s, user_id: int, job: db.Job, style: str = "professional") -> db.CoverLetterVersion:
    from .ai import service
    p, _ = profiles.current(s, user_id)
    user = (f"CANDIDATE PROFILE:\n{p.model_dump_json(exclude={'personal': {'address'}})[:9000]}\n\n"
            f"JOB: {job.title} at {job.company}\n{service.untrusted(job.description, 8000)}")
    with events.step(s, user_id, "cover_letter", f"Writing a {style} cover letter for {job.title} at {job.company}", skill="job-application-assistant"):
        d = service.structured(s, user_id, "cover letter", _system(style), user, Draft)
    cl = _letter(s, user_id, job, style)
    return _save(s, cl, d.paragraphs, "generated", check(d.paragraphs, p, job))


def edit(s, user_id: int, job: db.Job, paragraphs: list[str]) -> db.CoverLetterVersion:
    p, _ = profiles.current(s, user_id)
    cl = _letter(s, user_id, job, current(s, user_id, job.id)[0].style if current(s, user_id, job.id)[0] else "professional")
    return _save(s, cl, paragraphs, "edited", check(paragraphs, p, job))


def regenerate_paragraph(s, user_id: int, job: db.Job, index: int, instruction: str = "") -> db.CoverLetterVersion:
    from .ai import service
    cl, v = current(s, user_id, job.id)
    if not v or not 0 <= index < len(v.paragraphs): raise LookupError("No such paragraph")
    p, _ = profiles.current(s, user_id)
    user = (f"CANDIDATE PROFILE:\n{p.model_dump_json(exclude={'personal': {'address'}})[:8000]}\n\nJOB: {job.title} at {job.company}\n"
            f"{service.untrusted(job.description, 6000)}\n\nWHOLE LETTER:\n" + "\n\n".join(v.paragraphs) +
            f"\n\nRewrite paragraph {index + 1} only." + (f" The candidate asks: {instruction[:300]}" if instruction else ""))
    new = service.structured(s, user_id, "cover letter paragraph", _system(cl.style), user, OneParagraph).paragraph
    paras = list(v.paragraphs); paras[index] = new
    return _save(s, cl, paras, "regenerated", check(paras, p, job))


def current(s, user_id: int, job_id: int) -> tuple[db.CoverLetter | None, db.CoverLetterVersion | None]:
    cl = s.scalar(select(db.CoverLetter).where(db.CoverLetter.user_id == user_id, db.CoverLetter.job_id == job_id))
    if not cl: return None, None
    v = s.scalar(select(db.CoverLetterVersion).where(db.CoverLetterVersion.cover_letter_id == cl.id).order_by(db.CoverLetterVersion.number.desc()))
    return cl, v


def history(s, cl_id: int) -> list[db.CoverLetterVersion]:
    return list(s.scalars(select(db.CoverLetterVersion).where(db.CoverLetterVersion.cover_letter_id == cl_id).order_by(db.CoverLetterVersion.number.desc())))


def text_of(v: db.CoverLetterVersion, name: str) -> str:
    return "Dear Hiring Manager,\n\n" + "\n\n".join(v.paragraphs) + f"\n\nKind regards,\n{name}"


def export_pdf(s, user_id: int, v: db.CoverLetterVersion) -> Path:
    from fpdf import FPDF
    p, _ = profiles.current(s, user_id)
    f = config.user_dir(user_id, "letters") / f"cover-{v.cover_letter_id}-v{v.number}.pdf"
    pdf = FPDF(format="A4"); pdf.set_margins(22, 20, 22); pdf.add_page()
    safe = lambda t: t.encode("latin-1", "replace").decode("latin-1")   # core fonts are Latin-1
    pdf.set_font("Helvetica", "B", 18); pdf.cell(0, 10, safe(p.personal.name), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10); pdf.cell(0, 6, safe(" | ".join(x for x in [p.personal.email, p.personal.phone, p.personal.linkedin] if x)), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6); pdf.set_font("Helvetica", "", 11)
    for para in text_of(v, p.personal.name).split("\n\n"):
        pdf.multi_cell(0, 6, safe(para)); pdf.ln(3)
    pdf.output(str(f))
    return f
