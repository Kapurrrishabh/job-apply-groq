"""Candidate profile: schema, versions, completeness, and merge-with-review for resume imports."""
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from . import db


class M(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Bullet(M):
    id: str
    text: str
    source: str = "manual"          # "manual" or "resume:<id>"


class Personal(M):
    name: str = ""
    email: str = ""
    phone: str = ""
    current_location: str = ""
    address: str = ""
    citizenship: str = ""
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""
    headline: str = ""


class Education(M):
    institution: str = ""
    degree: str = ""
    department: str = ""
    location: str = ""
    start: str = ""
    end: str = ""
    graduation_year: str = ""
    grade: str = ""                 # CGPA or percentage, as written
    status: str = ""                # e.g. "In progress"
    coursework: list[str] = []


class Experience(M):
    id: str
    company: str = ""
    title: str = ""
    employment_type: str = ""       # internship | full-time | part-time | volunteer | contract
    location: str = ""
    start: str = ""
    end: str = ""
    technologies: list[str] = []
    bullets: list[Bullet] = []


class Project(M):
    id: str
    name: str = ""
    subtitle: str = ""
    start: str = ""
    end: str = ""
    technologies: list[str] = []
    bullets: list[Bullet] = []


class Skills(M):
    languages: list[str] = []
    frameworks: list[str] = []
    databases: list[str] = []
    cloud: list[str] = []
    tools: list[str] = []
    ml_ai: list[str] = []
    soft: list[str] = []
    domain: list[str] = []
    self_reported: list[str] = []   # experience the person reports without a CV project: forms yes, CV bullets no


class Language(M):
    name: str
    level: str = ""


class Preferences(M):
    roles: list[str] = []
    industries: list[str] = []
    locations: list[str] = []
    work_modes: list[Literal["remote", "hybrid", "onsite"]] = []
    job_types: list[Literal["internship", "full-time", "part-time", "contract"]] = []
    expected_salary: str = ""
    salary_floor_lpa: dict[str, float] = Field(default_factory=lambda: {"software": 0, "other": 0})
    max_years_required: int = 1
    notice_period: str = ""
    availability: str = ""
    company_size_min: int = 1
    service_firms: list[str] = []   # companies or keywords that are deal-breakers


class Facts(M):
    """What a resume can state. Preferences are your own choices, so they are never read from a resume."""
    personal: Personal = Personal()
    education: list[Education] = []
    experience: list[Experience] = []
    projects: list[Project] = []
    skills: Skills = Skills()
    languages: list[Language] = []
    certifications: list[str] = []
    achievements: list[str] = []
    publications: list[str] = []
    work_style: str = ""


class Profile(Facts):
    preferences: Preferences = Preferences()


# ---------- versions ----------

def current(s, user_id: int) -> tuple[Profile, int]:
    v = s.scalar(select(db.ProfileVersion).where(db.ProfileVersion.user_id == user_id).order_by(db.ProfileVersion.version.desc()))
    return (Profile.model_validate(v.data), v.version) if v else (Profile(), 0)


def save(s, user_id: int, profile: Profile, source: str, note: str = "") -> int:
    latest = s.scalar(select(func.max(db.ProfileVersion.version)).where(db.ProfileVersion.user_id == user_id)) or 0
    s.add(db.ProfileVersion(user_id=user_id, version=latest + 1, data=profile.model_dump(), source=source, note=note))
    s.flush()
    return latest + 1


def versions(s, user_id: int) -> list[db.ProfileVersion]:
    return list(s.scalars(select(db.ProfileVersion).where(db.ProfileVersion.user_id == user_id).order_by(db.ProfileVersion.version.desc())))


# ---------- completeness ----------

CHECKS = [("name", 8, lambda p: p.personal.name), ("email", 8, lambda p: p.personal.email), ("phone", 6, lambda p: p.personal.phone),
          ("current location", 4, lambda p: p.personal.current_location), ("LinkedIn", 4, lambda p: p.personal.linkedin),
          ("education", 12, lambda p: p.education), ("experience or projects", 16, lambda p: p.experience or p.projects),
          ("skills", 12, lambda p: any(getattr(p.skills, f) for f in Skills.model_fields)), ("languages", 4, lambda p: p.languages),
          ("desired roles", 8, lambda p: p.preferences.roles), ("preferred locations", 6, lambda p: p.preferences.locations),
          ("job types", 4, lambda p: p.preferences.job_types), ("availability", 4, lambda p: p.preferences.availability),
          ("expected salary", 4, lambda p: p.preferences.expected_salary)]


def completeness(p: Profile) -> tuple[int, list[str]]:
    total = sum(w for _, w, _ in CHECKS)
    got = sum(w for _, w, f in CHECKS if f(p))
    return round(100 * got / total), [n for n, _, f in CHECKS if not f(p)]


# ---------- merge for resume imports ----------

def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def merge(current_p: Profile, parsed: Facts, source: str) -> tuple[Profile, list[dict]]:
    """Proposed profile plus a change list. Filled fields are never overwritten: a differing value becomes a
    'conflict' the user decides on. New entries, bullets and skills are added and labelled with `source`."""
    out = current_p.model_copy(deep=True)
    changes: list[dict] = []
    for f in Personal.model_fields:
        old, new = getattr(out.personal, f), getattr(parsed.personal, f)
        if new and not old:
            setattr(out.personal, f, new); changes.append({"path": f"personal.{f}", "kind": "fill", "old": "", "new": new})
        elif new and old and _norm(new) != _norm(old):
            changes.append({"path": f"personal.{f}", "kind": "conflict", "old": old, "new": new})

    def merge_entries(kind: str, key):
        mine = getattr(out, kind)
        for e in getattr(parsed, kind):
            match = next((m for m in mine if key(m) == key(e)), None)
            if not match:
                e = e.model_copy(deep=True)
                for b in getattr(e, "bullets", []): b.source = source
                mine.append(e); changes.append({"path": kind, "kind": "add", "old": "", "new": key(e)}); continue
            if hasattr(e, "bullets"):
                have = {_norm(b.text) for b in match.bullets}
                for b in e.bullets:
                    if _norm(b.text) not in have:
                        nb = Bullet(id=f"{match.id}-{len(match.bullets) + 1}", text=b.text, source=source)
                        match.bullets.append(nb); changes.append({"path": f"{kind}.{match.id}", "kind": "add", "old": "", "new": b.text})
            for f in type(e).model_fields:
                if f in ("id", "bullets"): continue
                old, new = getattr(match, f), getattr(e, f)
                if new and not old: setattr(match, f, new); changes.append({"path": f"{kind}.{f}", "kind": "fill", "old": "", "new": str(new)})

    merge_entries("experience", lambda e: (_norm(e.company), _norm(e.title)))
    merge_entries("projects", lambda e: _norm(e.name))
    merge_entries("education", lambda e: (_norm(e.institution), _norm(e.degree)[:20]))
    for f in Skills.model_fields:
        have = {_norm(x) for x in getattr(out.skills, f)}
        added = [x for x in getattr(parsed.skills, f) if _norm(x) not in have]
        if added:
            getattr(out.skills, f).extend(added); changes.append({"path": f"skills.{f}", "kind": "add", "old": "", "new": ", ".join(added)})
    for f in ("certifications", "achievements", "publications"):
        have = {_norm(x) for x in getattr(out, f)}
        added = [x for x in getattr(parsed, f) if _norm(x) not in have]
        if added: getattr(out, f).extend(added); changes.append({"path": f, "kind": "add", "old": "", "new": "; ".join(added)})
    have = {_norm(l.name) for l in out.languages}
    for l in parsed.languages:
        if _norm(l.name) not in have: out.languages.append(l); changes.append({"path": "languages", "kind": "add", "old": "", "new": l.name})
    return out, changes


def apply_conflicts(proposed: Profile, changes: list[dict], accepted: set[int]) -> Profile:
    """Take the resume's value for the conflicts the user ticked (by index into `changes`)."""
    p = proposed.model_copy(deep=True)
    for i, c in enumerate(changes):
        if c["kind"] == "conflict" and i in accepted and c["path"].startswith("personal."):
            setattr(p.personal, c["path"].split(".", 1)[1], c["new"])
    return p
