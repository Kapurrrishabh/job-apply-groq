"""Jobs you paste in (LinkedIn, Naukri, Internshala, any site). Nothing is fetched from the platform."""
import hashlib
import re
from .base import Adapter, clean, employment, work_mode

HOSTS = {"linkedin.com": "linkedin", "naukri.com": "naukri", "internshala.com": "internshala", "wellfound.com": "wellfound"}


def platform_for(url: str) -> str:
    from .ats import parse_link
    if parse_link(url): return "employer"
    host = re.sub(r"^https?://(www\.)?", "", (url or "").lower()).split("/")[0]
    return next((v for k, v in HOSTS.items() if host.endswith(k)), "other")


class Manual(Adapter):
    key = "manual"

    def normalize_job(self, raw: dict) -> dict:
        url, text = clean(raw.get("url")), raw.get("description") or ""
        if not clean(raw.get("title")): raise ValueError("A job needs at least a title")
        sid = re.search(r"(\d{6,})", url).group(1) if re.search(r"(\d{6,})", url) else hashlib.sha256((url or text[:500]).encode()).hexdigest()[:20]
        from .ats import parse_link
        link = parse_link(url)
        if link: sid = f"{link[1]}/{link[2]}"
        return {"platform": raw.get("platform") or platform_for(url), "source_id": sid, "url": url, "apply_url": url, "ats": link[0] if link else "",
                "title": clean(raw["title"]), "company": clean(raw.get("company")), "location": clean(raw.get("location")),
                "work_mode": work_mode(text + " " + clean(raw.get("location"))), "employment_type": employment(text[:2000] + " " + clean(raw["title"])),
                "experience": "", "salary_text": clean(raw.get("salary")), "description": text[:40000], "skills": [], "posted_at": None,
                "closing_at": None, "active": True, "raw": {"imported": "manual"}}
