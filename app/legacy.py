"""One-time import of the pre-database data folder (data/*.yaml, tracker.csv, CVs, .env key, .sessions) into a user."""
import csv
import hashlib
import json
import re
import shutil
from pathlib import Path

import yaml
from sqlalchemy import select

from . import config, crypto, db
from .profile import Bullet, Education, Experience, Language, Personal, Preferences, Profile, Project, Skills
from . import profile as profiles

LEGACY = config.ROOT / "data"


def available(root: Path = LEGACY) -> bool:
    return (root / "profile.yaml").exists()


def to_profile(d: dict, source: str = "manual") -> Profile:
    """Map the old YAML profile layout onto the current schema."""
    idn, av, pr, sk = d.get("identity", {}), d.get("availability", {}), d.get("preferences", {}), d.get("skills", {})
    bullets = lambda e: [Bullet(id=b.get("id") or f"b{i}", text=b["text"], source=source) for i, b in enumerate(e.get("bullets", [])) if b.get("text")]
    kind = lambda t: "internship" if "intern" in (t or "").lower() else "volunteer" if "volunteer" in (t or "").lower() else "full-time"
    return Profile(
        personal=Personal(name=idn.get("name", ""), email=idn.get("email", ""), phone=str(idn.get("phone", "")),
                          current_location=idn.get("current_city", "") or idn.get("city", ""), address=idn.get("address", ""),
                          citizenship=idn.get("citizenship", ""), linkedin=idn.get("linkedin", ""), github=idn.get("github", ""),
                          headline=idn.get("headline", "")),
        education=[Education(institution=e.get("institution", ""), degree=e.get("degree", ""), location=e.get("location", ""),
                             start=e.get("start", ""), end=e.get("end", ""), graduation_year=(e.get("end") or "").split(" ")[-1],
                             grade=str(e.get("grade", "")), status=e.get("status", "")) for e in d.get("education", [])],
        experience=[Experience(id=e.get("id") or f"exp{i}", company=e.get("company", ""), title=e.get("title", ""), employment_type=kind(e.get("title")),
                               location=e.get("location", ""), start=e.get("start", ""), end=e.get("end", ""), bullets=bullets(e))
                    for i, e in enumerate(d.get("experience", []))],
        projects=[Project(id=p.get("id") or f"proj{i}", name=p.get("name", ""), subtitle=p.get("subtitle", ""), start=p.get("start", ""),
                          end=p.get("end", ""), bullets=bullets(p)) for i, p in enumerate(d.get("projects", []))],
        skills=Skills(languages=sk.get("languages", []), frameworks=sk.get("backend_web", []), ml_ai=sk.get("ml_ai", []),
                      cloud=sk.get("cloud_tools", []), self_reported=sk.get("self_reported", [])),
        languages=[Language(name=l["name"], level=l.get("level", "")) for l in d.get("languages", []) if l.get("name")],
        certifications=d.get("certifications", []), achievements=d.get("awards", []), work_style=(d.get("work_style") or "").strip(),
        preferences=Preferences(roles=pr.get("roles", []), locations=pr.get("locations", []), expected_salary=pr.get("expected_salary", ""),
                                salary_floor_lpa=pr.get("salary_floor_lpa") or {"software": 0, "other": 0},
                                max_years_required=pr.get("max_years_required", 1), notice_period=av.get("notice_period", ""),
                                availability="; ".join(x for x in [f"Internship: {av['internship_window']}" if av.get("internship_window") else "",
                                                                   f"Full-time from {av['full_time_from']}" if av.get("full_time_from") else ""] if x),
                                company_size_min=pr.get("company_size_min", 1), service_firms=pr.get("service_firms", []),
                                job_types=["internship", "full-time"] if pr.get("open_to_internships") else ["full-time"]))


def fill_gaps(base: Profile, older: Profile) -> Profile:
    """Keep `base` (the newest CV) and fill only the fields it leaves empty from `older`."""
    out = base.model_copy(deep=True)
    for f in Personal.model_fields:
        if not getattr(out.personal, f) and getattr(older.personal, f): setattr(out.personal, f, getattr(older.personal, f))
    for f in ("languages", "achievements", "certifications", "publications"):
        if not getattr(out, f): setattr(out, f, getattr(older, f))
    if not out.work_style: out.work_style = older.work_style
    if not out.skills.self_reported: out.skills.self_reported = older.skills.self_reported
    for e in out.education:
        twin = next((o for o in older.education if o.end == e.end and o.start == e.start), None)
        if twin and not e.status: e.status = twin.status
    return out


def _resume_row(s, user_id: int, src: Path, label: str, kind: str, default: bool) -> db.Resume:
    data = src.read_bytes(); sha = hashlib.sha256(data).hexdigest()
    have = s.scalar(select(db.Resume).where(db.Resume.user_id == user_id, db.Resume.sha256 == sha))
    if have: return have
    dest = config.user_dir(user_id, "resumes") / f"{sha[:16]}{src.suffix.lower()}"
    dest.write_bytes(data)
    from .resumes import extract_text
    r = db.Resume(user_id=user_id, label=label, filename=dest.name, mime="application/pdf", size=len(data), sha256=sha,
                  text=extract_text(dest), kind=kind, is_default=default, approved=True)
    s.add(r); s.flush(); return r


SENSITIVE = re.compile(r"(?i)ctc|salary|compensation|gender|race|ethnic|veteran|disabilit|sponsor|authori|legally|government|securities|non.?compete")


def run(s, user_id: int, root: Path = LEGACY, committed_profile: Path | None = None) -> dict:
    """Import everything; safe to call once. Returns counts for the UI."""
    counts = {}
    current = yaml.safe_load((root / "profile.yaml").read_text())
    newest = to_profile(current, source="resume:import")
    if committed_profile and committed_profile.exists():
        older = to_profile(yaml.safe_load(committed_profile.read_text()))
        profiles.save(s, user_id, older, "legacy", "Profile before the November CV import")
        profiles.save(s, user_id, newest, "import", "Imported from the November CV (as saved by the old app)")
        newest = fill_gaps(newest, older)
    profiles.save(s, user_id, newest, "merge", "Newest CV, with fields it lacks filled from the earlier profile")
    counts["profile_versions"] = len(profiles.versions(s, user_id))

    rules = yaml.safe_load((root / "answers.yaml").read_text()).get("rules", []) if (root / "answers.yaml").exists() else []
    for i, r in enumerate(rules):
        s.add(db.AnswerRule(user_id=user_id, position=i, pattern=r["match"], answer=str(r["answer"]), note=r.get("note", ""),
                            sensitive=bool(SENSITIVE.search(r["match"]))))
    counts["answer_rules"] = len(rules)

    cv = root / "state" / "import.pdf"
    if cv.exists(): _resume_row(s, user_id, cv, "CV (November 2026)", "uploaded", True)
    master = root / "cv" / "main_example.pdf"
    if master.exists(): _resume_row(s, user_id, master, "Master CV (October 2026)", "uploaded", not cv.exists())
    tailored = sorted((root / "cv").glob("main_*.pdf"))
    for p in tailored:
        if p.name != "main_example.pdf": _resume_row(s, user_id, p, p.stem.replace("main_", "").replace("_", " ").title(), "tailored", False)
    counts["resumes"] = len(tailored) + (1 if cv.exists() else 0)

    tracker = root / "tracker.csv"
    n = 0
    if tracker.exists():
        for row in csv.DictReader(tracker.open()):
            src = row.get("source") or f"{row['company']}|{row['role']}"
            job = s.scalar(select(db.Job).where(db.Job.user_id == user_id, db.Job.platform == "imported", db.Job.source_id == src[:300]))
            if not job:
                job = db.Job(user_id=user_id, platform="imported", source_id=src[:300], url=src if src.startswith("http") else "",
                             title=row["role"], company=row["company"], description="", raw={"tracker": row})
                s.add(job); s.flush()
            state = {"applied": "Submitted", "drafted": "Selected", "withdrawn": "Blocked"}.get(row["status"], "Discovered")
            if not s.scalar(select(db.Application).where(db.Application.user_id == user_id, db.Application.job_id == job.id)):
                s.add(db.Application(user_id=user_id, job_id=job.id, state=state, platform=row.get("channel") or "imported",
                                     score_at_apply=int(row["fit_rating"]) if (row.get("fit_rating") or "").isdigit() else None,
                                     evidence={"kind": "imported", "detail": f"Recorded in the earlier tracker on {row['date']}: {row.get('notes', '')[:300]}"},
                                     blocker="Posting closed before submission" if state == "Blocked" else ""))
                n += 1
    counts["applications"] = n

    key = config._env_file().get("GROQ_API_KEY")
    if key:
        from .ai import keys
        keys.store(s, user_id, key); counts["groq_key"] = 1

    from . import site_sessions
    for f in (config.ROOT / ".sessions").glob("*.json") if (config.ROOT / ".sessions").exists() else []:
        site_sessions.save(s, user_id, f.stem, json.loads(f.read_text())["cookies"])
    old_profile = config.ROOT / ".browser-profile"
    dest = config.instance() / "users" / str(user_id) / "browser-profile"
    if old_profile.exists() and not dest.exists():
        shutil.move(str(old_profile), dest)
    s.commit()
    return counts
