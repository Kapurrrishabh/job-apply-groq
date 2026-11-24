# Production readiness report

Date: 2026-11-24. Scope: a personal / household app running on one computer (`127.0.0.1` only). Not a hosted service.

## Verdict

Ready for personal and family use on your own machine. The full apply journey works end to end against a local job
board with a real browser. Live submission to real employer systems (Greenhouse, Lever, Ashby) and Wellfound has not
been re-run since the rebuild; treat the first few real applications as supervised (see "Not verified").

## Test evidence

Run: `python -m pytest -q` on macOS, Python 3.13.15, FastAPI 0.163.1, SQLAlchemy 2.1.9, Chrome headless.
Result: **111 passed, 0 failed** in 21 s.

| File | Tests | What it covers |
|---|---|---|
| `tests/test_core.py` | 49 | state machine, evidence rules, sensitive questions, form decisions, hybrid ranking, profile merge and versions, upload validation, cover-letter evidence check, CV tailoring filters, strict Groq schemas, encryption, log redaction, skill registry, agent tool schema |
| `tests/test_groq.py` | 11 | rejected key, 429 retry with `retry-after`, rate limit that never clears, timeout, strict vs JSON mode per model, key only in the auth header, malformed output repaired once, malformed twice fails, no key, encrypted key storage, prompt-injection fencing |
| `tests/test_web.py` | 33 | sign-in redirect, wrong and short passwords, CSRF rejection, other-host and other-origin rejection, all 13 pages render with security headers, key never shown back, per-user isolation (pages, bulk actions, agent tools), profile save and bad YAML, answers, resume upload and fake-PDF refusal, resume import needs review, Apply needs the confirmation tick, assisted platforms are prepared not submitted, agent shortlists but cannot submit, database backup before migration |
| `tests/test_e2e.py` | 9 | headless Chrome against `tests/mock_board.py`: the 20-step journey below, closed posting, CAPTCHA pause, unknown and sensitive questions then resume, missing confirmation then user confirmation, duplicate repost, daily limit, Groq rate limit during apply, crash recovery |
| `tests/test_gates.py` | 9 | location, pay, experience and company filters |

**The 20-step journey** (`test_full_journey`): register → save Groq key → test connection → upload resume → Groq reads
it → review and accept → profile saved → add an answer → search the board → duplicate marked → rank → explained score
→ write letter → edit letter (version 2) → shortlist → queue → authorize → browser fills and submits → verified with
reference `TB-…` and screenshot → board received the exact name, email, phone, PDF and letter → My Applications shows
the snapshot, the exact resume file and the screenshot → delete removes the record and files.

## Live checks outside the test suite

| Check | Result |
|---|---|
| Every strict JSON schema the app sends, against the real Groq API (`openai/gpt-oss-120b`) | 7 of 7 accepted after one fix (below); tool calling returned a valid `profile_summary` call |
| "Import earlier data" dry run into a throwaway instance with the real earlier data | 3 profile versions (100% complete, 18 bullets), 33 answers, 23 resumes, 51 applications, Groq key; real browser profile untouched |

## Defects found and fixed during verification

| Defect | How it was found | Fix |
|---|---|---|
| Groq rejected the resume-reading schema (a free-form salary map), so resume import would fail for everyone | live schema check | resumes are read into `Facts` (no preferences); a test now walks every schema sent to Groq |
| A failed agent tool call could leave part of its change behind (one job shortlisted while reporting an error) | agent isolation test | failed tool calls roll back |
| Another user's job id produced a server error instead of "not found" | isolation test | `LookupError` maps to a 404 page |
| Two copies of the worker loop (test and real) | review | one shared `_drain` / `_process` |

## Security summary

- Loopback only: `run.py` refuses other hosts; host and origin checks on every request; CSRF token on every form.
- argon2id passwords, HttpOnly SameSite=Strict session cookie, 14-day sessions.
- Groq keys, site logins and application snapshots are Fernet-encrypted with `instance/master.key` (0600).
- Strict CSP (`script-src 'self'`), `X-Frame-Options: DENY`, `nosniff`; job links pass a scheme check.
- Uploads: type, size (5 MB), PDF magic bytes, DOCX structure and zip-bomb guard.
- Job text and resume text go to Groq fenced as untrusted data; the agent has no submit tool; skills are data only.
- Keys, passwords, tokens and cookies are redacted from every event before storage.
- No CAPTCHA or OTP bypass, no stealth, no proxies. Platform automation follows each site's terms (`PLATFORM_POLICIES.md`).

## Not verified

| Item | Why | How to confirm |
|---|---|---|
| Real submissions on Greenhouse, Lever, Ashby and Wellfound since the rebuild | needs your authorization per application | apply to 2-3 real jobs and watch the Console; check each one's evidence |
| Tailored CV PDF build | not run in tests (needs TeX and a live Groq call) | open a job, press *Tailor my CV*, review and approve |
| Windows and Linux | only macOS was run | run the test suite there |
| Naukri terms | page returned HTTP 403 | stays assisted until read |

## Rollback

The previous version is in `../job-apply-groq-backup-20261124-1321/` and in the local git branch `pre-rebuild`.
The database is backed up to `instance/backups/` before every migration.
