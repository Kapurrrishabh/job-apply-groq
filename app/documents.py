"""Tailored one-page CV for one job (moderncv, lualatex). Groq only picks and orders your verified bullets; it never
writes new ones. The result is saved as an unapproved resume so you see the changes before it can be used."""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from pydantic import BaseModel
from pypdf import PdfReader

from . import db, events, letters, profile as profiles, resumes
from .agent import skills

TEX_ESCAPES = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
               "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def tex(s: str) -> str:
    s = re.sub(r"\s*—\s*", ", ", s or "").replace("–", "-")
    return "".join(TEX_ESCAPES.get(c, c) for c in s)


def tex_bin(name: str) -> str:
    for c in (shutil.which(name), f"/Library/TeX/texbin/{name}", f"/usr/bin/{name}"):
        if c and Path(c).exists(): return c
    raise RuntimeError(f"{name} not found. Install TeX Live or MacTeX (see README) to build tailored CVs.")


def compile_pdf(tex_file: Path, engine: str, runs: int = 2) -> Path:
    for _ in range(runs):
        r = subprocess.run([tex_bin(engine), "-interaction=nonstopmode", tex_file.name], cwd=tex_file.parent,
                           capture_output=True, text=True, timeout=180)
    pdf = tex_file.with_suffix(".pdf")
    if not pdf.exists(): raise RuntimeError(f"{engine} failed:\n{r.stdout[-1500:]}")
    return pdf


def pages(pdf: Path) -> int:
    return len(PdfReader(str(pdf)).pages)


class Competency(BaseModel):
    label: str
    items: str


class Section(BaseModel):
    id: str
    bullet_ids: list[str]


class Plan(BaseModel):
    profile_line: str
    competencies: list[Competency]
    sections: list[Section]


def plan(s, user_id: int, job: db.Job, p: profiles.Profile) -> Plan:
    from .ai import service
    ids = {e.id: [b.id for b in e.bullets] for e in p.experience + p.projects}
    system = ("Tailor a one-page CV. For every experience and project id, choose which bullet ids to keep and their order "
              "(most relevant first; keep at least one bullet per entry). Write a 2-line profile statement (max 230 characters) "
              "and 3-4 competency lines (label + comma-separated items) using only skills from the profile, with the posting's own "
              "words where the profile genuinely matches. No em-dashes.\n"
              f"Writing guidance:\n{skills.guidance('job-application-assistant', 2500)}")
    user = (f"AVAILABLE IDS: {ids}\n\nPROFILE:\n{p.model_dump_json(exclude={'personal': {'address'}})[:9000]}\n\n"
            f"JOB: {job.title} at {job.company}\n{service.untrusted(job.description, 7000)}")
    with events.step(s, user_id, "cv.plan", f"Choosing CV content for {job.title} at {job.company}", skill="job-application-assistant"):
        return checked(service.structured(s, user_id, "cv tailoring", system, user, Plan), p)


def checked(pl: Plan, p: profiles.Profile) -> Plan:
    """Drop anything the profile does not back: unknown bullet ids, unknown skills, invented numbers."""
    facts = p.model_dump_json()
    known = {b.id for e in p.experience + p.projects for b in e.bullets}
    for sec in pl.sections: sec.bullet_ids = [b for b in sec.bullet_ids if b in known]
    lower = facts.lower()
    for c in pl.competencies:
        c.items = ", ".join(i.strip() for i in c.items.split(",") if i.strip() and i.strip().lower().split(" (")[0] in lower)
    pl.competencies = [c for c in pl.competencies if c.items]
    if letters.numbers(pl.profile_line) - letters.numbers(facts): pl.profile_line = p.personal.headline
    return pl


def diff(pl: Plan, p: profiles.Profile) -> list[dict]:
    """Per entry: the bullets kept (in CV order) and the ones left out, for the review page."""
    chosen = {sec.id: sec.bullet_ids for sec in pl.sections}
    out = []
    for e in p.experience + p.projects:
        by_id = {b.id: b.text for b in e.bullets}
        keep = [b for b in chosen.get(e.id) or list(by_id)[:1] if b in by_id]
        out.append({"entry": getattr(e, "company", "") or getattr(e, "name", ""), "kept": [by_id[b] for b in keep],
                    "dropped": [t for b, t in by_id.items() if b not in keep]})
    return out


def _entries(entries: list, chosen: dict, cap: int | None) -> str:
    out = []
    for e in entries:
        by_id = {b.id: b.text for b in e.bullets}
        keep = [b for b in chosen.get(e.id) or list(by_id)[:1] if b in by_id][: cap or None]
        title, org = getattr(e, "title", "") or getattr(e, "subtitle", ""), getattr(e, "company", "") or getattr(e, "name", "")
        items = "\n".join(f"    \\item {tex(by_id[b])}" for b in keep)
        out.append(f"\\item{{\\cventry{{{tex(e.start)} - {tex(e.end)}}}{{{tex(title)}}}{{{tex(org)}}}{{{tex(getattr(e, 'location', ''))}}}{{}}{{\\vspace{{1pt}}\n\\begin{{itemize}}\n{items}\n\\end{{itemize}}}}}}")
    return "\n\\vspace{3pt}\n".join(out)


def render(pl: Plan, p: profiles.Profile, project_cap: int | None = None, exp_cap: int | None = None) -> str:
    me = p.personal
    first, _, last = me.name.partition(" ")
    chosen = {sec.id: sec.bullet_ids for sec in pl.sections}
    comp = "\n".join(f"\\item \\textbf{{{tex(c.label)}}}: {tex(c.items)}." for c in pl.competencies)
    edu = p.education[0] if p.education else profiles.Education()
    edu_line = tex(" ".join(x for x in [f"{edu.status}, expected {edu.end}." if edu.status else "", f"{edu.grade}." if edu.grade else ""] if x))
    honors = [". ".join(p.achievements)] if p.achievements else []
    extra = (["Certifications: " + ", ".join(p.certifications)] if p.certifications else []) + \
            ([f"Languages: {', '.join(f'{l.name} ({l.level.lower()})' for l in p.languages if l.name)}"] if p.languages else [])
    if extra: honors.append(". ".join(extra))
    template = (Path(__file__).parent / "latex" / "cv.tex").read_text()
    return (template.replace("<<FIRST>>", tex(first)).replace("<<LAST>>", tex(last)).replace("<<PHONE>>", tex(me.phone))
            .replace("<<EMAIL>>", tex(me.email)).replace("<<LINKEDIN>>", me.linkedin).replace("<<GITHUB>>", me.github)
            .replace("<<NAME>>", tex(me.name)).replace("<<PROFILE>>", tex(pl.profile_line)).replace("<<COMPETENCIES>>", comp)
            .replace("<<EXPERIENCE>>", _entries(p.experience, chosen, exp_cap)).replace("<<PROJECTS>>", _entries(p.projects, chosen, project_cap))
            .replace("<<EDU_DATES>>", f"{tex(edu.start)} - {tex(edu.end)}").replace("<<EDU_DEGREE>>", tex(edu.degree))
            .replace("<<EDU_SCHOOL>>", tex(edu.institution)).replace("<<EDU_PLACE>>", tex(edu.location)).replace("<<EDU_LINE>>", edu_line)
            .replace("<<HONORS>>", "\n".join(f"\\item {{{tex(h)}}}" for h in honors)))


def tailor(s, user_id: int, job: db.Job) -> db.Resume:
    p, version = profiles.current(s, user_id)
    pl = plan(s, user_id, job, p)
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "cv.tex"
        # Trim the least relevant bullets until it fits on one page.
        for project_cap, exp_cap in [(None, None), (1, None), (1, 4), (1, 3), (1, 2)]:
            f.write_text(render(pl, p, project_cap, exp_cap))
            pdf = compile_pdf(f, "lualatex")
            if pages(pdf) == 1: break
        else:
            raise RuntimeError("The CV is still longer than one page; shorten some bullets in your profile")
        r = resumes.add(s, user_id, "cv.pdf", pdf.read_bytes(), f"CV for {job.company or job.title}", kind="tailored", job_id=job.id, approved=False)
    r.parsed = {"tailoring": {"profile_version": version, "profile_line": pl.profile_line,
                              "competencies": [c.model_dump() for c in pl.competencies], "diff": diff(pl, p)}}
    return r
