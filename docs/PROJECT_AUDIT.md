# Project audit (2026-11-24)

Scope: `job-apply-groq` (this repo, 3 local commits, never pushed) and the skills repository it was built from,
`ai-job-search` (https://github.com/ai-job-search-dev/ai-job-search, MIT, local checkout at commit `83c788d`, 2026-10-27).

## Architecture as found

| Layer | Implementation | Notes |
|---|---|---|
| Web | FastAPI + Jinja2 templates + one CSS file, vanilla JS polling | Loopback-only (`run.py` refuses other hosts); Host/Origin check middleware |
| Storage | YAML (`profile`, `answers`, `settings`), CSV tracker, JSON job cache under `data/` | Single user; no transactions; no history |
| Secrets | Groq key in `.env` (0600); site cookies in `.sessions/*.json` (0600, plaintext) | Not encrypted |
| AI | `llm.py` posts to Groq `/openai/v1/chat/completions`, JSON-schema strict on gpt-oss models | No usage tracking, no provider abstraction, no tool calling |
| Ranking | `gates.py` deterministic filters + `assist.score_job` LLM scores (4 dims, fixed weights) | Weights hard-coded; no requirement-level output |
| Documents | `documents.py` LaTeX CV (bullet selection by id) + cover letter, fact guard on numbers | Needs TeX; no versions, no editing |
| Automation | One worker thread owning a Playwright persistent Chrome; adapters for LinkedIn, Wellfound, Naukri, Internshala, employer ATS (Greenhouse/Ashby/Lever) | In-memory queue; success inferred from page text |
| Tests | 28 pytest tests (gates, answers, LinkedIn parsing, documents, sessions, web) | No browser, integration or end-to-end tests |

## Skills repository (ai-job-search @ 83c788d)

| Skill | Kind | Access method | Status in this app |
|---|---|---|---|
| `linkedin-search` | Bun CLI | LinkedIn guest HTML endpoints | Ported to Python (`linkedin_guest.py`). **Conflicts with LinkedIn User Agreement §8.2** (scraping) |
| `freehire-search` | Bun CLI | freehire.me public JSON API (aggregates ~50 ATS sources) | Not used yet. Compliant discovery source |
| `instahyre-search` | Bun CLI | Instahyre listing pages | Not used |
| `jobbank/jobdanmark/jobindex/jobnet-search` | Bun CLI | Danish boards | Out of scope (India search) |
| `job-application-assistant` | Knowledge skill (Markdown) | n/a | Evaluation framework, CV/letter rules partly reused |
| `job-scraper`, `upskill` | Knowledge skills | n/a | Not used |

Third-party skill files are instructions and code written for Claude Code. They are treated as untrusted input:
the app never executes their shell commands; executable behaviour is re-implemented in Python behind a tool registry.

## What works
- Loopback web UI, Host/Origin guard, secrets kept out of git.
- LinkedIn Easy Apply automation and Wellfound apply (used for 30 real applications on 2026-11-03).
- Deterministic gates (location, service firm, years, pay, language), tested.
- One-page LaTeX CV and cover letter rendering, tested with a hand-made plan.
- Saved site logins with a 48-hour floor, restored on browser start.

## Partially works
- Naukri and Internshala adapters: selectors never verified against live pages.
- Employer ATS filler: written, never run against a real form.
- Autopilot: written, never run end to end.
- Groq features: implemented, never exercised with a real key in tests.

## Broken
- **CV import overwrites the profile.** Importing a CV replaced the whole profile and wiped address, citizenship,
  headline, languages, awards and work style (observed 2026-11-23). Tests then failed because bullet ids changed.
- **Tests depend on the live `data/` folder**, so editing your own profile breaks the suite.
- **LinkedIn search via the signed-in page** collected the wrong links (2 jobs, wrong city); replaced by the guest
  API, which ignores the experience filter.
- **Success detection** for Naukri/Internshala/ATS is a text heuristic; nothing records evidence.

## Missing (against the master specification)
User accounts and isolation; database and migrations; encrypted secrets; resume manager (DOCX, versions, preview);
profile versions and merge review; configurable hybrid ranking; cover letter studio (styles, versions, paragraph
regeneration); explicit application state machine with persistence, locking and crash recovery; submission evidence
and snapshots; My Applications preview; agent orchestrator with tool calling and a skill registry; execution
console fed by real events; diagnostics; mock job site and browser/end-to-end tests; platform policy layer.

## Privacy finding
(Resolved: see KNOWN_ISSUES #7.) The three local commits contain personal data (`data/profile.yaml`, CVs, tracker). The master specification forbids
committing personal data, so history will be rebuilt without it before any push (see ARCHITECTURE_DECISIONS ADR-009).
