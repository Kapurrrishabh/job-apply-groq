# Architecture decisions

**ADR-001 Keep FastAPI + server-rendered Jinja pages.** The existing UI works and is simple to run anywhere; a
JavaScript SPA would add a build step without a user-facing gain. Interactive parts (console, queue) use small
vanilla JS with fetch/Server-Sent Events.

**ADR-002 SQLite with SQLAlchemy 2 and a minimal migration runner.** One file, no server, fits personal/family use.
Migrations are ordered Python functions keyed by `PRAGMA user_version`; the database file is copied to
`instance/backups/` before each migration. Alembic was considered and rejected as heavier than the need.

**ADR-003 Candidate profile is a versioned JSON document validated by Pydantic**, not a dozen normalised tables.
The profile is edited and reviewed as a whole, every save creates a new version, and applications snapshot the
version they used. Bullets carry ids and a `source` (resume id or "manual") for provenance.

**ADR-004 Authentication: local accounts, argon2id, server-side sessions.** This is a self-hosted app outside any
company identity system, so it uses the mechanism native to that context: per-user passwords hashed with argon2id
(argon2-cffi), a random 256-bit session token in an HttpOnly, SameSite=Strict cookie, stored only as a SHA-256 hash,
and a per-session CSRF token on every form. The listener stays on 127.0.0.1.

**ADR-005 Secrets are encrypted with Fernet** (cryptography). Groq keys, saved site cookies and application
snapshots are encrypted at rest. Losing `instance/master.key` makes them unreadable by design.

**ADR-006 Platform automation follows each platform's terms** (docs/PLATFORM_POLICIES.md). LinkedIn, Naukri and
Internshala run in assisted mode. Wellfound and employer job systems allow paced automation after explicit
authorization. freehire.me's public API is the automated discovery source.

**ADR-007 One browser worker thread.** Playwright's sync API is single-threaded and concurrent sessions against the
same sites invite rate limits. Tasks are persisted in the database, executed one at a time, and recovered on start.

**ADR-008 Third-party skills are data, not code.** Skill metadata is copied from ai-job-search at a pinned commit with
its licence and SHA recorded. Executable skills are re-implemented in Python and exposed only through the tool
registry; the LLM can never run shell commands, read arbitrary files or drive the browser directly.

**ADR-009 Personal data never enters git.** All user data moves to `instance/` (git-ignored). The repository ships an
example profile instead. Local history containing personal data is rebuilt before the first push.

**ADR-010 The LLM never decides that a submission succeeded.** Success is decided by deterministic checks on the
page (platform-specific confirmation patterns, URLs, reference numbers) or by the user's explicit confirmation.
