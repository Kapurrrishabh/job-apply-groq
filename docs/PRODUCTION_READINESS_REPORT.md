# Production readiness report

Date: 2026-11-24. Scope: a personal / household app running on one computer (`127.0.0.1` only). Not a hosted service.

## Verdict

Ready for personal and family use on your own machine. Every apply path works end to end in a real browser against a
local job board, the UI was driven click by click, and the Groq calls and a real employer form were checked live.
Still to watch: the first real submissions on Lever, Ashby and Wellfound (see "Not verified").

## Test evidence

`python -m pytest -q`: **157 passed, 0 failed**, on macOS with Python 3.13.15 (the main environment) and again with
Python 3.12.14 in a fresh virtual environment built only from `requirements.txt` (113 s).

| File | Tests | What it covers |
|---|---|---|
| `test_core.py` | 50 | state machine, evidence rules, sensitive questions, form decisions, hybrid ranking, profile merge and versions, upload validation, letter number check (by value), CV tailoring filters, every Groq schema is strict, encryption, log redaction, skill registry, agent tools |
| `test_groq.py` | 13 | rejected key, 429 with `retry-after`, rate limit that never clears, timeout, strict vs JSON mode, key only in the auth header, malformed output repaired, Groq `json_validate_failed` retried, no key, encrypted key, injection fencing, output budget |
| `test_web.py` | 38 | sign-in, CSRF, other host/origin refused, 13 pages render, every template link and form has a route, every label is linked to its field, isolation (pages, bulk, agent), profile, answers, uploads, import review, Apply needs the tick, assisted sites, agent cannot submit, migration backup, test board hidden, earlier data imported once, tailored CV built with real TeX (one page, waits for approval) |
| `test_e2e.py` | 27 | headless Chrome against `tests/mock_board.py` (below) |
| `test_ui.py` | 20 | Playwright clicking through the app (below) |
| `test_gates.py` | 9 | location, pay, experience and company filters |

**Apply scenarios** (`test_e2e.py`): the 20-step journey; closed posting; CAPTCHA wording; visible "I am not a robot"
box; "protected by reCAPTCHA" notice does *not* stop it; unknown + sensitive questions, answered, then submitted; no
confirmation, then you confirm; server error after Submit; duplicate repost; daily limit; Groq rate limit and rejected key
during apply; restart recovery; no resume; missing phone; form without a resume field; radio, checkbox and select from
saved answers; consent box never ticked without your answer; confident AI answer; one click with mixed outcomes; board
down then retry; stopping a waiting run; stopping a running batch; crash mid-batch; application locked by another run;
Wellfound waits for sign-in; approved tailored CV sent instead of the default (unapproved one is not); two people apply
with their own details.

**UI scenarios** (`test_ui.py`): sign up, out, wrong password, back in; deep link returns after sign-in; every menu page
with zero console or CSP errors; five pages and the waiting cards fit a phone; the whole journey by clicking (key,
connection test, resume upload and import review, answers, search with the live console, select-all, rank, letter
write/edit/rewrite, the required confirmation tick, apply, evidence screenshot, exact resume download, delete with the
confirm dialog cancelled then accepted); answering questions from the queue; CAPTCHA handed to you with the menu
counter; unconfirmed then confirmed; assisted LinkedIn job prepared, you submit; removing from the queue; the Stop
button; console level filter; agent run shown live; clear messages for bad inputs; a second person sees only their data.

## Live checks outside the test suite

| Check | Result |
|---|---|
| Every strict schema the app sends, against the real Groq API (`openai/gpt-oss-120b`) | 7 of 7 accepted; tool calling works |
| Ranking, cover letter, resume reading and a two-step agent run through the app's code with real Groq | all succeeded; ranking ignored a "give this job 100" instruction planted in the posting |
| freehire.me public API search | 75 jobs across 16 sources |
| Real Greenhouse form, read only (nothing typed or sent) | no false CAPTCHA; 7 fields filled from the profile, resume upload found, the one personal question left for you |
| "Import earlier data" dry run into a throwaway instance | 3 profile versions (100%), 33 answers, 23 resumes, 51 applications, Groq key; real browser profile untouched |

## Defects found and fixed during verification

| Defect | Found by | Fix |
|---|---|---|
| Groq rejected the resume-reading schema, so resume import failed for everyone | live schema check | resumes read into `Facts`; test walks every schema |
| Cover letters failed intermittently: hidden reasoning used up the 2,000-token budget | live Groq run | default budget 6,000 (measured); Groq JSON failures get the one repair retry |
| Letter check flagged true numbers written differently ("12,000" vs "12k") | live Groq run | numbers compared by value |
| Every Greenhouse application would stop on an invisible reCAPTCHA | live read-only form check | pause only for a challenge that is shown; hidden helper fields ignored |
| Stopped or crashed runs left jobs stuck in Queued forever | scenario review | they return to the shortlist |
| A failed agent tool call could leave half its change | agent test | failed calls roll back |
| Another person's job id caused a server error | isolation test | 404 page |
| Messages were lost on pages with a `#section` link | Playwright | message goes before the `#` |
| Labels not linked to fields (screen readers, clicking a label) | Playwright | every label linked; test added |
| Menu bottom (Sign out, Diagnostics) unreachable on short screens | Playwright | menu scrolls |
| Pages scrolled sideways on phones; buttons cut off on waiting cards | Playwright, screenshots | phone layout fixed; tests added |
| Job page score squeezed into a corner | screenshots | score as a header line |
| Test board offered to real users | screenshots | shown only while tests run it |
| Any account could import one person's earlier data | review | import works once per installation |
| Missing site icon (a 404 on every page) | Playwright | icon added |
| Unused imports, duplicated worker loop | lint, review | removed / one shared loop |

## Security summary

- Loopback only: `run.py` refuses other hosts; host and origin checks on every request; CSRF token on every form.
- argon2id passwords, HttpOnly SameSite=Strict session cookie, 14-day sessions.
- Groq keys, site logins and application snapshots are Fernet-encrypted with `instance/master.key` (0600).
- Strict CSP (`script-src 'self'`), `X-Frame-Options: DENY`, `nosniff`; job links pass a scheme check.
- Uploads: type, size (5 MB), PDF magic bytes, DOCX structure and zip-bomb guard.
- Job and resume text go to Groq fenced as untrusted data; the agent has no submit tool; skills are data only.
- Keys, passwords, tokens and cookies are redacted from every event before storage.
- No CAPTCHA or OTP bypass, no stealth, no proxies. Platform automation follows each site's terms (`PLATFORM_POLICIES.md`).

## Not verified

| Item | Why | How to confirm |
|---|---|---|
| Real submissions on Lever, Ashby, Wellfound and Internshala | none appeared in today's searches / needs your sign-in and authorization | apply to 1-2 real jobs each and check the evidence |
| A real Greenhouse submission | the check was read-only by design | your first Greenhouse application: watch the Console |
| Windows and Linux; Python 3.11 | only macOS with 3.12 and 3.13 was run | run the test suite there |

## Rollback

The previous version is in `../job-apply-groq-backup-20261124-1321/` and in the local git branch `pre-rebuild`.
The database is backed up to `instance/backups/` before every migration.
