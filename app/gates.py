"""Hard filters run before any LLM scoring. Pure functions: no network, no disk."""
import re

IT_SERVICE_INDUSTRY = re.compile(r"(?i)staffing|recruit|outsourc")
SENIOR_TITLE = re.compile(r"(?i)\b(senior|sr\.?|lead|staff|principal|manager|head|director|architect|vp)\b|\b(ii|iii|iv)\b|\bsde ?[2-4]\b|\bl[4-7]\b")
IMMEDIATE = re.compile(r"(?i)immediate joiner|join immediately|immediately available|immediate start")
LANGS = ["German", "French", "Spanish", "Japanese", "Korean", "Mandarin", "Chinese", "Arabic", "Portuguese", "Italian",
         "Dutch", "Russian", "Polish", "Telugu", "Tamil", "Kannada", "Marathi", "Malayalam", "Bengali", "Gujarati"]


def looks_senior(title: str) -> bool:
    """Titles that are never entry level; skipped before fetching details."""
    return bool(SENIOR_TITLE.search(title or ""))


def min_years(text: str) -> int:
    v = [int(m.group(1)) for m in re.finditer(r"(?i)(\d+)\s*(?:\+|-|–|to)\s*(?:\d+\s*)?\+?\s*years?", text)]
    v += [int(m.group(1)) for m in re.finditer(r"(?i)(?:minimum|at least|min\.?)\s*(?:of\s*)?(\d+)\s*years?", text)]
    return min(v) if v else 0


def pay_lpa(text: str) -> tuple[float, float] | None:
    """(low, high) annual pay in lakhs from text like '₹10L – ₹15L', '12-18 LPA' or '₹40,000 per month'."""
    m = re.search(r"₹\s*([\d.]+)\s*L\s*[–-]\s*₹?\s*([\d.]+)\s*L", text)
    if m: return float(m.group(1)), float(m.group(2))
    m = re.search(r"(?i)([\d.]+)\s*(?:-|–|to)\s*([\d.]+)\s*(?:lpa|lakhs?)", text)
    if m: return float(m.group(1)), float(m.group(2))
    m = re.search(r"(?i)₹\s*([\d,]+)\s*(?:[–-]\s*₹?\s*([\d,]+))?\s*(?:/|per)\s*month", text)
    if m:
        lo = float(m.group(1).replace(",", "")); hi = float((m.group(2) or m.group(1)).replace(",", ""))
        return lo * 12 / 1e5, hi * 12 / 1e5
    return None


def evaluate(job: dict, prefs: dict, languages: list[str]) -> dict:
    """job: title, company, location, description, industries, salary, employment. Returns verdict + reasons."""
    desc = job.get("description") or ""
    text = " ".join(str(job.get(k) or "") for k in ("title", "salary", "description"))
    fails, flags = [], []

    locs = [l.lower() for l in prefs.get("locations", [])]
    loc = (job.get("location") or "").lower()
    if loc and not any(l in loc for l in locs):
        (flags if loc.strip() in ("india", "") else fails).append(f"location '{job.get('location')}' is not on your list")

    company = (job.get("company") or "").lower()
    if any(s.lower() in company for s in prefs.get("service_firms", [])):
        fails.append(f"{job.get('company')} is on your service-firm list")
    elif IT_SERVICE_INDUSTRY.search(job.get("industries") or ""):
        fails.append(f"industry '{job.get('industries')}' looks like staffing/outsourcing")
    elif "it services" in (job.get("industries") or "").lower():
        flags.append("tagged IT Services: check it is a product company")

    is_intern = "intern" in (job.get("title", "") + " " + (job.get("employment") or "")).lower()
    y = min_years(text)
    cap = prefs.get("max_years_required", 1)
    if not is_intern and y > cap + 1: fails.append(f"asks for {y}+ years")
    elif not is_intern and y > cap: flags.append(f"asks for {y}+ years (stretch)")

    pay = pay_lpa(text)
    floors = prefs.get("salary_floor_lpa", {})
    floor = floors.get("software" if re.search(r"(?i)software|sde|developer|backend|full.?stack", job.get("title", "")) else "other")
    if pay and floor and not is_intern and pay[1] < floor:
        fails.append(f"pay up to {pay[1]:g} LPA is below your {floor} LPA floor")

    known = {l.lower() for l in languages}
    for lang in LANGS:
        if lang.lower() not in known and re.search(rf"(?i)(fluen\w*|proficien\w*|speak\w*|native)\s+(in\s+)?{lang}|{lang}\s+(speaking|fluency|is (a )?must|required)", desc):
            fails.append(f"requires {lang}")

    if IMMEDIATE.search(desc): flags.append("wants an immediate joiner")

    verdict = "fail" if fails else "flag" if flags else "pass"
    return {"verdict": verdict, "reasons": fails + flags, "min_years": y, "pay_lpa": pay}
