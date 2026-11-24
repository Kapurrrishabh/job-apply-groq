"""Platform adapters and what each may automate (see docs/PLATFORM_POLICIES.md)."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Policy:
    name: str
    discovery: str      # api | page_import | manual_import | test
    applying: str       # automated | assisted
    terms_url: str
    terms_note: str
    domain: str = ""
    login_url: str = ""


PLATFORMS: dict[str, Policy] = {
    "freehire": Policy("freehire.me (employer job systems)", "api", "automated", "https://freehire.me",
                       "Public JSON API for agents; applications go to the employer's own job system", "freehire.me"),
    "wellfound": Policy("Wellfound", "page_import", "automated", "https://wellfound.com/terms",
                        "§III.6 allows automated use at human-like load; §III.4 forbids scraping, so jobs are imported one page at a time",
                        "wellfound.com", "https://wellfound.com/login"),
    "linkedin": Policy("LinkedIn", "manual_import", "assisted", "https://www.linkedin.com/legal/user-agreement",
                       "§8.2 forbids bots and automated methods, so the app prepares and you submit",
                       "linkedin.com", "https://www.linkedin.com/login"),
    "naukri": Policy("Naukri", "manual_import", "assisted", "https://www.naukri.com/termsconditions",
                     "Terms ban automated crawling and scraping and getting around its anti-robot measures, so the app prepares and you submit", "naukri.com", "https://www.naukri.com/nlogin/login"),
    "internshala": Policy("Internshala", "manual_import", "assisted", "https://internshala.com/terms",
                          "Irrelevant applications get accounts blocked and the site uses reCAPTCHA, so the app prepares and you submit", "internshala.com", "https://internshala.com/login/user"),
    "other": Policy("Any other site", "manual_import", "assisted", "", "Paste the job; the app prepares, you submit"),
    "testboard": Policy("Test board (local)", "test", "automated", "", "Local mock job board used by the test suite"),
}


def offered() -> dict[str, Policy]:
    """Platforms shown to people. The local test board only appears while the test suite runs one."""
    from .testboard import base
    return {k: p for k, p in PLATFORMS.items() if k != "testboard" or base()}


def adapters():
    from .ats import AUTOMATED, EmployerSystem
    from .freehire import Freehire
    from .manual import Manual
    from .testboard import TestBoard
    from .wellfound import Wellfound
    return {"freehire": Freehire(), "wellfound": Wellfound(), "testboard": TestBoard(), "manual": Manual(), "ats": EmployerSystem()}, AUTOMATED


def apply_adapter(job):
    """The adapter that may fill and submit this job automatically, or None when the platform is assisted."""
    ad, automated = adapters()
    if PLATFORMS.get(job.platform, PLATFORMS["other"]).applying != "automated": return None
    if job.platform == "freehire": return ad["ats"] if job.ats in automated else None
    return ad.get(job.platform)
