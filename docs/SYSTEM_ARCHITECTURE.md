# System architecture

```
Browser (you) ──HTTP, 127.0.0.1 only──▶ FastAPI app (app/web/*)
                                         │  auth + CSRF + Host/Origin guard, per-user access checks
                                         ▼
                              Services (app/services/*)  ── pure logic: ranking, gates, state machine, validation
                                 │            │              │
                    SQLite (app/db) │   AI layer (app/ai)   Browser worker (app/automation)
                    users, profiles,│   Groq provider,      one thread, one Playwright context per user,
                    resumes, jobs,  │   schema-validated    platform adapters, evidence capture
                    applications,   │   outputs, usage log
                    events …        │
                                    ▼
                         Agent orchestrator (app/agent): Groq tool calling ─▶ tool registry ─▶ permission check
                                                                              ─▶ service call ─▶ validated result
                                                                              ─▶ execution events (console)
```

## Components
- **app/db**: SQLAlchemy 2 models and a migration runner (`PRAGMA user_version`, backup before every migration).
- **app/auth**: local accounts (argon2id), server-side sessions (random token, stored hashed), CSRF tokens.
- **app/crypto**: Fernet encryption for Groq keys, saved site cookies and application snapshots. The master key
  lives in `instance/master.key` (0600) or `JOBAPPLY_MASTER_KEY`.
- **app/profile**: Pydantic candidate profile, versions, completeness score, CV import with merge review.
- **app/resumes**: PDF/DOCX upload (size and type checked), text extraction, versions, default resume, preview.
- **app/ai**: provider interface (`GroqProvider`, `FakeProvider` for tests), JSON-schema outputs validated with
  Pydantic, rate-limit backoff, token/temperature settings, usage records with redacted logs.
- **app/ranking**: deterministic features (skills, experience, seniority, location, employment type, pay,
  education) + Groq semantic scores (role alignment, project relevance, industry), user-configurable weights.
- **app/letters**: cover letter studio (styles, versions, paragraph regeneration, evidence check, PDF export).
- **app/platforms**: adapters behind one interface (`search_jobs`, `get_job_details`, `normalize_job`,
  `check_availability`, `prepare_application`, `submit_application`, `verify_submission`) and the policy table in
  `docs/PLATFORM_POLICIES.md`.
- **app/automation**: browser worker, persisted queue, application state machine, locks, retries, evidence.
- **app/agent**: skill registry (pinned ai-job-search skills + provenance), tool registry with JSON schemas and
  permissions, Groq tool-calling loop. The LLM chooses tools; the app executes and validates them.
- **app/events**: execution events with correlation IDs, streamed to the console page.

## Data isolation
Every row carries `user_id`; every query goes through helpers that filter on the signed-in user. Files live under
`instance/users/<user_id>/`. Each user has their own browser profile directory and saved logins.

## Application states
Discovered → Ranked → Selected → Queued → Preflight → Preparing → Filling → (Awaiting User) → Ready to Submit →
Submitting → Submitted | Submission Unconfirmed | Failed | Blocked | Duplicate.
Only verified evidence (a confirmation page pattern, a reference number, or your explicit confirmation for assisted
platforms) moves an application to **Submitted**. A crash during *Submitting* becomes *Submission Unconfirmed*; it is
never retried automatically.
