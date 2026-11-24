# Implementation roadmap

| Phase | Work | Status |
|---|---|---|
| 1 | Audit, architecture, platform terms | done |
| 2 | Package layout, database + migrations, instance folder, legacy data import | done |
| 3 | Accounts, sessions, CSRF, isolation; profile versions + merge-review import; resume manager (PDF/DOCX) | done |
| 4 | AI layer (provider interface, validation, usage), hybrid ranking with weights, cover letter studio | done |
| 5 | Discovery: freehire API, manual import (LinkedIn/Naukri/Internshala), Wellfound page import, test board; normalisation, dedup, saved searches, bookmarks | done |
| 6 | Skill registry (pinned ai-job-search), tool registry, Groq tool-calling agent | done |
| 7 | Browser worker v2: persisted queue, state machine, locks, preflight, adapters (test board, Wellfound, Greenhouse/Ashby/Lever), evidence | done |
| 8 | Application snapshots, My Applications, submission preview, deletion | done |
| 9 | UI pages and console wired to real events | done |
| 10 | Security pass: isolation, prompt injection, logging redaction, upload limits | done |
| 11 | Unit, integration, browser (mock site) and end-to-end tests | done |
| 12 | Install docs, health checks, backup/restore, readiness report | done |

All phases are done; evidence is in [PRODUCTION_READINESS_REPORT.md](PRODUCTION_READINESS_REPORT.md). Next: supervised real applications on employer systems and Wellfound (KNOWN_ISSUES #10).
