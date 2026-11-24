"""Deterministic submission verification. The LLM never decides success."""
import re
from dataclasses import dataclass

# Patterns the platforms (and the employer systems behind freehire) show after a real submission.
CONFIRM = {
    "greenhouse": [r"(?i)thank you for applying", r"(?i)application (has been )?(received|submitted)"],
    "lever": [r"(?i)application submitted", r"(?i)thanks for applying"],
    "ashby": [r"(?i)application (was )?submitted", r"(?i)thanks for (your )?appl(ying|ication)"],
    "wellfound": [r"(?i)application sent", r"(?i)you applied"],
    "testboard": [r"(?i)application received\. reference (TB-\d+)"],
}
REFERENCE = re.compile(r"(?i)(?:reference|application id|confirmation (?:number|code))[:\s#]+([A-Z0-9-]{4,})")


@dataclass
class Verdict:
    confirmed: bool
    detail: str
    reference: str = ""


def verify(kind: str, page_text: str, url: str = "") -> Verdict:
    """Confirmed only when a known confirmation pattern for this system is on the page."""
    for pat in CONFIRM.get(kind, []):
        m = re.search(pat, page_text or "")
        if m:
            ref = REFERENCE.search(page_text or "")
            return Verdict(True, f"page shows '{m.group(0)[:80]}'" + (f" at {url}" if url else ""), ref.group(1) if ref else "")
    return Verdict(False, "no confirmation message found on the page")
