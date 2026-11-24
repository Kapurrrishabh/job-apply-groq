"""Skill registry: metadata from the vendored ai-job-search skills (pinned commit) joined to the Python tools that
implement them. Skill files are data. Nothing in them is executed, and their instructions never override app rules."""
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

VENDOR = Path(__file__).parent / "vendor" / "ai-job-search"

# skill name -> (status, tools that implement it, why)
BINDINGS = {
    "freehire-search": ("enabled", ["search_jobs", "job_details"], "Public freehire.me API, re-implemented in Python (app/platforms/freehire.py)"),
    "linkedin-search": ("disabled", [], "LinkedIn User Agreement §8.2 forbids automated access; LinkedIn jobs are imported manually"),
    "job-application-assistant": ("enabled", ["rank_jobs", "write_cover_letter"], "Evaluation framework and writing rules used as guidance text"),
    "job-scraper": ("enabled", ["refine_queries"], "Search-query design guidance used when refining searches"),
}


@dataclass
class Skill:
    name: str
    description: str
    version: str
    status: str
    tools: list[str]
    reason: str
    path: str
    guidance: str = field(repr=False, default="")


def _front_matter(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    out = {}
    if not m: return out
    key = None
    for line in m.group(1).splitlines():
        kv = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if kv:
            key, val = kv.group(1), kv.group(2).strip()
            out[key] = "" if val in (">", "|") else val.strip("'\"")
        elif key and line.startswith(" "):
            out[key] = (out[key] + " " + line.strip()).strip()
    return out


@lru_cache
def registry() -> dict[str, Skill]:
    out = {}
    for name, (status, tools, reason) in BINDINGS.items():
        f = VENDOR / name / "SKILL.md"
        if not f.exists(): continue
        meta = _front_matter(f.read_text())
        extra = "".join(p.read_text() for p in sorted((VENDOR / name).glob("0*.md")))
        out[name] = Skill(name=name, description=meta.get("description", "")[:400], version=meta.get("version", ""), status=status,
                          tools=tools, reason=reason, path=str(f.relative_to(VENDOR.parent.parent.parent)), guidance=extra[:20000])
    return out


def provenance() -> dict:
    return json.loads((VENDOR / "PROVENANCE.json").read_text())


def guidance(skill: str, limit: int = 6000) -> str:
    sk = registry().get(skill)
    return sk.guidance[:limit] if sk and sk.status == "enabled" else ""
