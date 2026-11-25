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
    opt_in: str = ""    # set when you may switch on automation despite the terms; says what you accept


PLATFORMS: dict[str, Policy] = {
    "web": Policy("Web search (Brave Search API)", "api", "automated", "https://brave.com/search/api/",
                  "Finds job pages on employer job systems, Internshala, and LinkedIn when you switched it on; needs your Brave Search API key"),
    "freehire": Policy("freehire.me (employer job systems)", "api", "automated", "https://freehire.me",
                       "Public JSON API for agents; applications go to the employer's own job system", "freehire.me"),
    "wellfound": Policy("Wellfound", "page_import", "automated", "https://wellfound.com/terms",
                        "§III.6 allows automated use at human-like load; §III.4 forbids scraping, so jobs are imported one page at a time",
                        "wellfound.com", "https://wellfound.com/login"),
    "linkedin": Policy("LinkedIn", "manual_import", "assisted", "https://www.linkedin.com/legal/user-agreement",
                       "§8.2 forbids bots and automated methods, so the app prepares and you submit unless you switch on Easy Apply automation",
                       "linkedin.com", "https://www.linkedin.com/login",
                       opt_in="LinkedIn's User Agreement §8.2 forbids bots, and LinkedIn may restrict or suspend accounts that use them"),
    "naukri": Policy("Naukri", "manual_import", "assisted", "https://www.naukri.com/termsconditions",
                     "Terms ban automated crawling and scraping and getting around its anti-robot measures, so the app prepares and you submit", "naukri.com", "https://www.naukri.com/nlogin/login"),
    "internshala": Policy("Internshala", "page_import", "automated", "https://internshala.com/terms",
                          "Terms don't forbid automation but block accounts that send irrelevant applications, so the app applies only to jobs you pick, "
                          "one at a time; jobs are imported one page at a time", "internshala.com", "https://internshala.com/login/user"),
    "employer": Policy("Company job site (Greenhouse, Lever, Ashby)", "manual_import", "automated", "",
                       "The employer's own job system: paste the link and the app applies after you authorize"),
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
    from .internshala import Internshala
    from .linkedin import LinkedIn
    from .manual import Manual
    from .testboard import TestBoard
    from .wellfound import Wellfound
    employer = EmployerSystem()
    return {"freehire": Freehire(), "wellfound": Wellfound(), "testboard": TestBoard(), "internshala": Internshala(), "linkedin": LinkedIn(), "manual": Manual(), "ats": employer,
            "employer": employer}, AUTOMATED


def apply_adapter(job, opted=frozenset()):
    """The adapter that may fill and submit this job automatically, or None when you submit it yourself.
    `opted` holds the platforms you switched automation on for (only those whose policy allows the choice)."""
    ad, automated = adapters()
    pol = PLATFORMS.get(job.platform, PLATFORMS["other"])
    if pol.applying != "automated" and not (pol.opt_in and job.platform in opted): return None
    if job.platform in ("freehire", "employer"): return ad["ats"] if job.ats in automated else None
    return ad.get(job.platform)
