"""Application-form filling. Each field is classified first, then filled from (in order) the verified profile,
the user's saved answers, or a confident profile-based AI answer (never for sensitive questions). Anything else is
reported as missing so the application waits for the user."""
import re
from dataclasses import dataclass, field
from typing import Callable

from .. import answers as answer_rules
from ..profile import Profile

IDENTITY = [
    ("first_name", r"first name|given name"), ("last_name", r"last name|family name|surname"), ("full_name", r"^(full |your )?name\b|full name"),
    ("email", r"e-?mail"), ("phone", r"phone|mobile|contact number"), ("linkedin", r"linkedin"), ("github", r"github"),
    ("portfolio", r"portfolio|website|personal site"), ("location", r"(current )?(location|city)\b"),
    ("current_company", r"current (company|employer)|^employer"), ("current_title", r"current (title|role|position)|job title"),
    ("school", r"school|university|college|institution"), ("degree", r"^degree|degree\b"), ("discipline", r"discipline|major|field of study|branch"),
    ("grad_year", r"graduation (year|date)|year of graduation|passing year"), ("gpa", r"\b(c?gpa|grade|percentage)\b"),
]
RESUME = re.compile(r"(?i)resume|\bcv\b|curriculum")
COVER = re.compile(r"(?i)cover letter|covering letter|why (do you want|should we hire|are you interested)|motivation")


def classify(question: str) -> str:
    q = " ".join((question or "").lower().replace("*", " ").split())
    if COVER.search(q): return "cover_letter"
    if answer_rules.is_sensitive(q): return "sensitive"
    for cat, pat in IDENTITY:
        if re.search(pat, q): return cat
    return "other"


def identity_value(cat: str, p: Profile) -> str | None:
    first, _, last = p.personal.name.partition(" ")
    exp = next((e for e in p.experience if re.search(r"(?i)present|current", e.end)), p.experience[0] if p.experience else None)
    edu = p.education[0] if p.education else None
    return {"first_name": first, "last_name": last, "full_name": p.personal.name, "email": p.personal.email, "phone": p.personal.phone,
            "linkedin": p.personal.linkedin, "github": p.personal.github, "portfolio": p.personal.portfolio or p.personal.github,
            "location": p.personal.current_location, "current_company": exp.company if exp else None, "current_title": exp.title if exp else None,
            "school": edu.institution if edu else None, "degree": edu.degree if edu else None,
            "discipline": (edu.department or "Computer Science") if edu and re.search(r"(?i)comput", edu.degree) else (edu.department if edu else None),
            "grad_year": (edu.graduation_year or edu.end.split(" ")[-1]) if edu else None, "gpa": edu.grade if edu else None}.get(cat) or None


@dataclass
class FormContext:
    profile: Profile
    rules: list
    resume_path: str
    cover_text: str = ""
    cover_path: str | None = None
    ai_answer: Callable[[str, list[str] | None], str | None] | None = None   # non-sensitive questions only


@dataclass
class FillResult:
    answers: list[dict] = field(default_factory=list)    # {question, answer, source, field_type}
    missing: list[dict] = field(default_factory=list)    # {question, options, sensitive}
    resume_uploaded: bool = False


def decide(question: str, options: list[str] | None, ctx: FormContext) -> tuple[str | None, str]:
    """(answer, source) for one question, or (None, reason)."""
    cat = classify(question)
    if cat == "cover_letter": return (ctx.cover_text or None, "cover letter") if not options else (None, "choice")
    if cat not in ("sensitive", "other"):
        v = identity_value(cat, ctx.profile)
        if v: return (answer_rules.pick(v, options), "profile") if options else (v, "profile")
    rule = answer_rules.lookup(ctx.rules, question, options)
    if rule: return rule, "rule"
    if cat == "sensitive": return None, "sensitive: needs your saved answer"
    if ctx.ai_answer:
        a = ctx.ai_answer(question, options)
        if a and (not options or a in options): return a, "ai"
    return None, "unknown"


def fill(b, ctx: FormContext) -> FillResult:
    res = FillResult()
    fields = b.fields()
    groups: dict[str, list[dict]] = {}
    for f in fields:
        if f["type"] in ("radio",) and f["name"]: groups.setdefault(f["name"], []).append(f)

    def record(q, a, src, kind): res.answers.append({"question": q, "answer": a, "source": src, "field_type": kind})

    for f in fields:
        q = " ".join((f["label"] or f["aria"] or f["placeholder"] or f["name"]).replace("*", " ").split())
        if f["tag"] == "button" or f["type"] in ("submit", "button", "search", "radio") or not q: continue
        if f["type"] == "file":
            if RESUME.search(q + f["name"] + f["id"]):
                b.upload(f["sel"], ctx.resume_path); res.resume_uploaded = True; record(q, ctx.resume_path.rsplit("/", 1)[-1], "resume", "file")
            elif ctx.cover_path and COVER.search(q + f["name"] + f["id"]):
                b.upload(f["sel"], ctx.cover_path); record(q, ctx.cover_path.rsplit("/", 1)[-1], "cover letter", "file")
            continue
        if f["type"] == "checkbox":
            a, src = decide(q, ["Yes", "No"], ctx)
            if a == "Yes": b.check(f["sel"]); record(q, "Yes", src, "checkbox")
            elif f["required"]: res.missing.append({"question": q, "options": ["Yes", "No"], "sensitive": answer_rules.is_sensitive(q)})
            continue
        if f["tag"] == "select":
            opts = [o for o in (f["options"] or []) if o and not re.match(r"(?i)^(select|choose|--|please)", o)]
            a, src = decide(q, opts, ctx)
            if a: b.select(f["sel"], a); record(q, a, src, "select")
            elif f["required"]: res.missing.append({"question": q, "options": opts, "sensitive": answer_rules.is_sensitive(q)})
            continue
        if f["value"]: record(q, f["value"], "prefilled", "text"); continue
        a, src = decide(q, None, ctx)
        if a:
            if f.get("combo"): b.type(f["sel"], a); b.wait(900); b.press("Enter")
            else: b.fill(f["sel"], a)
            record(q, a, src, "textarea" if f["tag"] == "textarea" else "text")
        elif f["required"]:
            res.missing.append({"question": q, "options": None, "sensitive": answer_rules.is_sensitive(q)})

    for name, radios in groups.items():
        q = " ".join((radios[0]["group"] or name).replace("*", " ").split())
        opts = [r["label"] for r in radios]
        a, src = decide(q, opts, ctx)
        if a:
            b.check(next(r["sel"] for r in radios if r["label"] == a)); record(q, a, src, "radio")
        elif any(r["required"] for r in radios):
            res.missing.append({"question": q, "options": opts, "sensitive": answer_rules.is_sensitive(q)})
    return res
