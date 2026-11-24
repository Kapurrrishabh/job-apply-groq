# Known issues

| # | Issue | Status |
|---|---|---|
| 1 | CV import overwrote the whole profile | fixed: merge with review; conflicts only change when you tick them |
| 2 | Tests read the live data folder | fixed: each test gets its own instance, key and database |
| 3 | LinkedIn guest API ignored the experience filter | removed: LinkedIn is manual import and assisted applying (§8.2) |
| 4 | Naukri terms could not be fetched (HTTP 403) | open: Naukri stays assisted until verified |
| 5 | Submission success inferred from page text without evidence | fixed: per-system confirmation patterns, screenshot, otherwise "Submission Unconfirmed" |
| 6 | Saved site cookies stored unencrypted | fixed: Fernet-encrypted per user |
| 7 | Local git history contains personal data | fixed: published history starts from a clean commit; the old history stays local only |
| 8 | Links from job data were not scheme-checked | fixed: `safe_url` on every external link |
| 9 | Resume-reading schema rejected by Groq strict mode | fixed: `Facts` model; schema test added |
| 10 | Real submissions on Lever, Ashby and Wellfound not re-run since the rebuild | open: supervise the first few; a real Greenhouse form was checked read-only |
| 11 | Tailored CVs need TeX installed | by design: the app says so; cover letters use plain PDFs and need nothing extra |
| 12 | One browser worker for all users on a computer | by design (ADR-007): runs are queued in order |
| 13 | Invisible reCAPTCHA stopped every Greenhouse application | fixed: only a challenge that is shown pauses the run |
| 14 | Long hidden reasoning could cut cover letters short | fixed: 6,000-token default (measured) and one retry |
| 15 | Stopped or crashed runs left jobs stuck in Queued | fixed: they return to the shortlist |
| 16 | Phone layout, unlinked labels, lost messages on `#section` pages | fixed; covered by Playwright tests |
