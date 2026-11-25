"""Web search for job pages through the official Brave Search API. Google's own search API is closed to new users
and scraping Google breaks its terms. Only pages the app can apply to are kept: employer job systems (read from their
public job boards), Internshala, and LinkedIn when you switched LinkedIn automation on."""
import time
import httpx
from . import ats
from .internshala import DETAIL as INTERNSHALA_PAGE, _is_internshala
from .linkedin import job_id as linkedin_job

API = "https://api.search.brave.com/res/v1/web/search"
SITES = {"employer": ["greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com"], "internshala": ["internshala.com/internship/detail", "internshala.com/job/detail"],
         "linkedin": ["linkedin.com/jobs/view"]}
PAGE_LIMIT, PAGE_PAUSE = 10, 4   # pages opened per search and seconds between them; provisional, human-paced


def queries(role: str, location: str, sites: list[str]) -> list[str]:
    where = "" if location.strip().lower() in ("", "india") else f" {location.strip()}"
    return [f'"{role.strip()}"{where} site:{site}' for site in sites]


def freshness(days: int) -> str:
    return next((code for limit, code in [(1, "pd"), (7, "pw"), (31, "pm")] if days <= limit), "py")


def kind_of(url: str) -> str | None:
    if ats.parse_link(url): return "employer"
    if INTERNSHALA_PAGE.search(url) and _is_internshala(url): return "internshala"
    return "linkedin" if linkedin_job(url) else None


def search(browser, params: dict, api_key: str, opted: frozenset) -> tuple[list[dict], dict]:
    """(jobs, counts of what was skipped and why). One Brave query per site; `site:` is supported, OR is not documented."""
    from . import adapters
    from .manual import Manual
    sites = SITES["employer"] + SITES["internshala"] + (SITES["linkedin"] if "linkedin" in opted else [])
    urls = []
    for q in queries(params["query"], params.get("location", ""), sites):
        r = httpx.get(API, params={"q": q, "count": 20, "freshness": freshness(int(params.get("days", 14)))},
                      headers={"X-Subscription-Token": api_key, "Accept": "application/json"}, timeout=15)
        if r.status_code in (401, 403): raise PermissionError("Brave rejected the search API key; check it on Settings")
        if r.status_code == 429: raise RuntimeError("Brave Search's limit for your key is used up for now; try again later")
        r.raise_for_status()
        urls += [x["url"] for x in (r.json().get("web") or {}).get("results", [])]
    found, skipped, pages = [], {"other sites": 0, "closed or unreadable": 0, "page limit": 0}, 0
    for url in dict.fromkeys(urls):
        kind = kind_of(url)
        if kind == "employer":
            try:
                found.append(Manual().normalize_job({**ats.posting(url), "url": url}))
            except (ValueError, httpx.HTTPError):
                skipped["closed or unreadable"] += 1
        elif kind and browser is not None and pages < PAGE_LIMIT:
            pages += 1; browser.goto(url); browser.wait(2500)
            try:
                found.append(adapters()[0][kind].import_current_page(browser))
            except ValueError:
                skipped["closed or unreadable"] += 1
            time.sleep(PAGE_PAUSE)
        else:
            skipped["page limit" if kind else "other sites"] += 1
    return found, {k: v for k, v in skipped.items() if v}
