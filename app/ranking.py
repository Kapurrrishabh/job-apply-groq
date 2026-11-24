"""Hybrid job ranking: deterministic features from structured data + Groq semantic assessment, user-weighted.
A dimension with no information scores None and is left out of the weighted average (unknown is not a mismatch)."""
import re
from datetime import date
from pydantic import BaseModel, Field

from .profile import Profile

DEFAULT_WEIGHTS = {"skills": 30, "experience": 20, "role": 15, "projects": 15, "education": 5, "industry": 5, "logistics": 10}

# canonical skill -> regex of how postings write it
SKILLS = {
    "python": r"python", "java": r"\bjava\b(?!script)", "javascript": r"javascript|\bjs\b|ecmascript", "typescript": r"typescript|\bts\b",
    "c++": r"c\+\+|\bcpp\b", "c": r"\bc\b(?![+#])", "c#": r"c#|\.net\b", "go": r"\bgolang\b|\bgo\b(?= |,|/)", "rust": r"\brust\b", "sql": r"\bsql\b",
    "react": r"react(\.js)?\b", "next.js": r"next\.?js", "node.js": r"node(\.js)?\b", "html": r"\bhtml5?\b", "css": r"\bcss3?\b",
    "fastapi": r"fastapi", "flask": r"\bflask\b", "django": r"django", "spring": r"spring( boot)?", "rest api": r"rest(ful)? ?api",
    "graphql": r"graphql", "postgresql": r"postgres", "mysql": r"mysql", "mongodb": r"mongo", "redis": r"\bredis\b", "sqlite": r"sqlite",
    "aws": r"\baws\b|amazon web services", "gcp": r"\bgcp\b|google cloud", "azure": r"\bazure\b", "docker": r"docker", "kubernetes": r"kubernetes|\bk8s\b",
    "terraform": r"terraform", "ci/cd": r"ci ?/ ?cd|continuous integration", "git": r"\bgit\b", "linux": r"\blinux\b",
    "machine learning": r"machine learning|\bml\b", "deep learning": r"deep learning", "pytorch": r"pytorch", "tensorflow": r"tensorflow",
    "scikit-learn": r"scikit|sklearn", "pandas": r"pandas", "numpy": r"numpy", "nlp": r"\bnlp\b|natural language", "computer vision": r"computer vision|opencv",
    "llm": r"\bllms?\b|large language model", "rag": r"\brag\b|retrieval.augmented", "hugging face": r"hugging ?face|transformers library",
    "langchain": r"langchain|langgraph", "spark": r"\bspark\b|pyspark", "kafka": r"kafka", "airflow": r"airflow", "pytest": r"pytest",
    "playwright": r"playwright", "selenium": r"selenium", "data structures": r"data structures|algorithms|\bdsa\b",
}
COMPILED = {k: re.compile(v, re.I) for k, v in SKILLS.items()}
SENIOR = re.compile(r"(?i)\b(senior|sr\.?|lead|staff|principal|manager|head|director|architect)\b|\b(ii|iii|iv)\b|\bsde ?[2-4]\b")


def skills_in(text: str) -> set[str]:
    return {k for k, rx in COMPILED.items() if rx.search(text or "")}


def candidate_text(p: Profile) -> str:
    parts = [x for f in type(p.skills).model_fields for x in getattr(p.skills, f)]
    for e in p.experience: parts += e.technologies + [b.text for b in e.bullets] + [e.title]
    for pr in p.projects: parts += pr.technologies + [b.text for b in pr.bullets] + [pr.name, pr.subtitle]
    return " \n".join(parts)


def _months(start: str, end: str) -> int:
    def parse(s):
        m = re.search(r"([A-Za-z]{3})[a-z]* (\d{4})", s or "")
        if not m:
            y = re.search(r"(\d{4})", s or ""); return (int(y.group(1)), 6) if y else None
        return int(m.group(2)), ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"].index(m.group(1).lower()) + 1
    a = parse(start); b = (date.today().year, date.today().month) if re.search(r"(?i)present|current|now", end or "") else parse(end)
    return max(0, (b[0] - a[0]) * 12 + b[1] - a[1]) if a and b else 0


def candidate_years(p: Profile) -> float:
    """Professional experience in years; internships count half, volunteering not at all."""
    total = 0.0
    for e in p.experience:
        w = 0 if e.employment_type == "volunteer" else 0.5 if e.employment_type == "internship" or "intern" in e.title.lower() else 1
        total += w * _months(e.start, e.end)
    return round(total / 12, 1)


def required_years(text: str) -> int | None:
    v = [int(m.group(1)) for m in re.finditer(r"(?i)(\d+)\s*(?:\+|-|–|to)\s*(?:\d+\s*)?\+?\s*(?:years?|yrs)", text or "")]
    v += [int(m.group(1)) for m in re.finditer(r"(?i)(?:minimum|at least|min\.?)\s*(?:of\s*)?(\d+)\s*(?:years?|yrs)", text or "")]
    return min(v) if v else None


class Dim(BaseModel):
    score: int | None
    weight: float
    basis: str


class RankResult(BaseModel):
    match_score: int
    confidence: str
    recommendation: str
    dimensions: dict[str, Dim]
    strengths: list[str]
    gaps: list[str]
    requirements_matched: list[str]
    requirements_missing: list[str]


class Semantic(BaseModel):
    """What Groq judges: meaning, not keyword counts."""
    role_alignment: int = Field(ge=0, le=100)
    project_relevance: int = Field(ge=0, le=100)
    industry_relevance: int | None = Field(default=None, ge=0, le=100)
    requirements_matched: list[str]
    requirements_missing: list[str]
    strengths: list[str]
    gaps: list[str]
    confidence: str


def deterministic(job: dict, p: Profile) -> dict[str, tuple[int | None, str]]:
    text = f"{job.get('title', '')}\n{job.get('description', '')}"
    req = set(job.get("skills_canonical") or []) | skills_in(text)
    have = skills_in(candidate_text(p))
    out = {}
    if req:
        hit = req & have
        out["skills"] = (round(100 * len(hit) / len(req)), f"{len(hit)} of {len(req)} required skills: has {', '.join(sorted(hit)) or 'none'}; missing {', '.join(sorted(req - have)) or 'none'}")
    else:
        out["skills"] = (None, "the posting lists no recognisable skills")
    need, yrs = required_years(text), candidate_years(p)
    intern = "intern" in (job.get("title", "") + " " + (job.get("employment_type") or "")).lower()
    if intern:
        out["experience"] = (100, "internship: no experience requirement applies")
    elif need is None and not SENIOR.search(job.get("title", "")):
        out["experience"] = (None, "no experience requirement stated")
    else:
        need = need if need is not None else 4
        sc = 100 if yrs >= need else max(0, round(100 - 30 * (need - yrs)))
        out["experience"] = (sc, f"asks for {need}+ years; you have about {yrs} (internships count half)")
    deg = re.search(r"(?i)\b(ph\.?d|master'?s|m\.?tech|bachelor'?s|b\.?tech|b\.?e\.?)\b", text)
    if not deg:
        out["education"] = (None, "no education requirement stated")
    else:
        mine = " ".join(f"{e.degree} {e.status}" for e in p.education).lower()
        level = deg.group(1).lower()
        if "ph" in level: out["education"] = (30, "asks for a PhD")
        elif "master" in level or "m.tech" in level: out["education"] = (50 if "master" not in mine else 100, "asks for a master's degree")
        else: out["education"] = (85 if "progress" in mine else 100, "asks for a bachelor's degree" + ("; yours is in progress" if "progress" in mine else ""))
    notes, scores = [], []
    loc = (job.get("location") or "").lower()
    if loc:
        ok = any(l.lower() in loc for l in p.preferences.locations) or ("remote" in loc and "remote" in [l.lower() for l in p.preferences.locations])
        scores.append(100 if ok else 0); notes.append(f"location {job.get('location')} {'is' if ok else 'is not'} on your list")
    et = (job.get("employment_type") or "").lower()
    if et and p.preferences.job_types:
        ok = any(t in et for t in p.preferences.job_types); scores.append(100 if ok else 30); notes.append(f"{et} {'matches' if ok else 'is not'} your job types")
    out["logistics"] = (round(sum(scores) / len(scores)) if scores else None, "; ".join(notes) or "location and job type not stated")
    return out


def combine(det: dict, sem: Semantic | None, weights: dict) -> RankResult:
    dims = {k: Dim(score=v[0], weight=weights.get(k, 0), basis=v[1]) for k, v in det.items()}
    if sem:
        dims["role"] = Dim(score=sem.role_alignment, weight=weights.get("role", 0), basis="Groq: how well the role matches your direction and past work")
        dims["projects"] = Dim(score=sem.project_relevance, weight=weights.get("projects", 0), basis="Groq: relevance of your projects to this work")
        dims["industry"] = Dim(score=sem.industry_relevance, weight=weights.get("industry", 0),
                               basis="Groq: relevance of your domain experience" if sem.industry_relevance is not None else "industry not clear from the posting")
    used = [d for d in dims.values() if d.score is not None and d.weight > 0]
    total_w = sum(d.weight for d in used)
    score = round(sum(d.score * d.weight for d in used) / total_w) if total_w else 0
    covered = total_w / max(sum(weights.values()), 1)
    conf = "high" if sem and covered >= 0.7 else "medium" if covered >= 0.45 else "low"
    rec = "Strong match" if score >= 75 else "Good match" if score >= 60 else "Moderate match" if score >= 45 else "Weak match"
    return RankResult(match_score=score, confidence=conf if not (sem and sem.confidence == "low") else "low", recommendation=rec, dimensions=dims,
                      strengths=(sem.strengths if sem else [])[:4], gaps=(sem.gaps if sem else [])[:4],
                      requirements_matched=sem.requirements_matched[:10] if sem else [], requirements_missing=sem.requirements_missing[:10] if sem else [])


def semantic(s, user_id: int, job: dict, p: Profile) -> Semantic:
    from .ai import service
    system = ("You assess how well a candidate fits a job. Judge capability, not keyword overlap: a candidate who built a "
              "FastAPI + PostgreSQL + LLM app fits backend and AI roles even if the posting words differ. Scores 0-100. "
              "role_alignment: the work this role does vs the candidate's experience and stated roles. project_relevance: "
              "how directly their projects and internships show they can do this work. industry_relevance: null when the "
              "industry is unclear. List up to 6 concrete requirements matched and missing, up to 4 strengths and gaps, "
              "each one short sentence citing the candidate's real evidence. confidence: low when the posting is vague. "
              "Use only facts in the candidate profile.")
    user = (f"CANDIDATE PROFILE:\n{p.model_dump_json(exclude={'personal': {'address', 'phone', 'email'}})[:9000]}\n\n"
            f"JOB POSTING:\n{service.untrusted(job.get('title', '') + chr(10) + (job.get('description') or ''), 9000)}")
    return service.structured(s, user_id, "job ranking", system, user, Semantic)
