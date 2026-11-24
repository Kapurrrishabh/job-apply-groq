# Platform terms and what the app automates (checked 2026-11-24)

| Platform | What the terms say | Discovery | Applying |
|---|---|---|---|
| LinkedIn | User Agreement §8.2: do not "use bots or other unauthorized automated methods to access the Services" and do not use "scripts, robots… browser plugins… to scrape or copy the Services". https://www.linkedin.com/legal/user-agreement | **Manual import**: paste a job URL and its text | **Assisted**: the app prepares the CV, cover letter and answers; you submit on LinkedIn and confirm in the app |
| Naukri | Terms (https://www.naukri.com/termsconditions, read in a browser 2026-11-24): do not "use automated means to crawl and/or scrape content"; "The Site uses technological means to exclude Robots etc. … You undertake not to circumvent these measures"; do not "use any device, software or routine to interfere" with the site. Applying is not named, but automation would run into the anti-robot measures | Manual import | Assisted |
| Internshala | Terms (https://internshala.com/terms, full text read 2026-11-24) say nothing about bots, scraping or automation. They do say "Irrelevant applications will result in your account being penalized (we may stop you from applying to future Jobs or block your account all together)". The site uses reCAPTCHA. (An earlier note here quoted a data-mining clause; it is not in the current terms.) | Manual import | Assisted (could be automated for jobs you pick: see below) |
| Wellfound | Terms §III.6 (https://wellfound.com/terms): do not "use any automated system… to access the Site or Services in a manner that… produces greater load… than a human can reasonably produce"; §III.4 forbids scraping content | **Import the page you are viewing**: browse Wellfound in the app's browser and press *Import this job* (one page, read on your click) | **Automated with your authorization**, human-paced, one application at a time |
| freehire.me | Public JSON API documented for agents (`/api/v1/agent/jobs/search`), open-source aggregator | **Automated** (public API) | Applies on the employer's own job system (below) |
| Employer job systems (Greenhouse, Lever, Ashby) | Applicant-facing forms on the employer's site; no applicant automation clause reviewed yet | via freehire or manual import | Greenhouse/Ashby/Lever: automated fill, submit only after your authorization; anything else assisted |
| Test board (local) | Ours | Automated | Automated (used by the test suite) |

Rules the app enforces on every platform:
- No CAPTCHA, OTP or verification bypass. When a challenge is shown (challenge wording, a visible "I am not a robot" box or a
  challenge popup), the run pauses with state **Awaiting User**. A "protected by reCAPTCHA" notice or an invisible check is not a
  challenge and does not pause the run; if the site shows a challenge after Submit, the application becomes **Submission Unconfirmed**.
- No stealth flags, fingerprint changes or proxies. The browser runs with its sandbox on.
- One browser task at a time, with platform-specific delays and a daily application limit.

To change a platform's mode, edit `app/platforms.py` after re-checking its terms; the UI never offers an automated
mode the table above does not allow.
