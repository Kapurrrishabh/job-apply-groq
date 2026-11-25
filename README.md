# job-apply-groq

A job-application assistant that runs on your own computer, with a web interface. Each person in a household gets
their own account, profile, resumes and Groq key. It finds jobs, scores how well each one fits you and explains why,
writes cover letters from your real experience, and fills in and submits applications where the site allows it.
The AI is [Groq](https://console.groq.com), which has a free API key.

**You stay in charge.** Nothing is submitted until you tick the jobs on the Queue page, tick the confirmation box and
press **Apply**. An application only counts as *Submitted* when the site shows a confirmation, or you confirm it yourself.

## What you need

| | Why | How |
|---|---|---|
| Python 3.12+ | runs the app | [python.org](https://www.python.org/downloads/) |
| Google Chrome | the browser the app fills forms in | [google.com/chrome](https://www.google.com/chrome/), or `playwright install chromium` |
| Groq API key | fit explanations, cover letters, resume reading, the agent | free at [console.groq.com/keys](https://console.groq.com/keys) |
| TeX (optional) | tailored one-page CVs | macOS: [MacTeX](https://tug.org/mactex/) · Linux: `sudo apt install texlive-full` · Windows: [MiKTeX](https://miktex.org) |

## Set up (about 5 minutes)

```bash
git clone https://github.com/Kapurrrishabh/job-apply-groq.git
cd job-apply-groq
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py                        # opens http://127.0.0.1:8000
```

The bar under the header shows your five steps (**Get ready → Find jobs → Shortlist → Apply → Track**) and always
points at the next one. The first time:

1. **Create your account.** The first visit asks for your email and a password (10+ characters).
2. **Settings & job sites** (account menu, top right): paste your Groq key and press *Test Groq connection*.
   Sign in to Internshala or Wellfound here if you use them.
3. **Resumes:** upload your resume (PDF or Word) and press *Update profile from this resume*. Review what it found;
   anything that disagrees with your profile is only changed if you tick it.
4. **My profile:** check your details and what you're looking for (roles, cities, job types, salary floor).
5. **Saved answers:** add answers to questions forms keep asking (notice period, expected salary, work authorization).
   Salary, visa, legal and demographic questions are only ever answered from here, never guessed.

## Daily use

1. **Find jobs:** search, paste a job from any site, or import the Internshala or Wellfound job open in the app's
   browser. Each job card shows a quick check against your rules.
2. Tick jobs → **Rank selected**. Each job gets a 0-100 match score with the reasons, strengths and gaps.
3. Open a job → **Write cover letter** (edit it, or rewrite one paragraph) and optionally **Tailor my CV**
   (it picks and orders your own bullets, shows you what changed, and waits for your approval).
4. Press **Shortlist** on the jobs you want → on **Shortlist**, tick the confirmation box → **Apply to selected**.
5. If a site shows a CAPTCHA or asks something the app doesn't know, the job moves to **Needs you** on the Shortlist
   page (the number in the header). Your answers are saved for next time. **Activity console** shows every step live.
6. **Applications** shows exactly what was sent: the resume file, cover letter, every answer and the confirmation.

The **AI agent** page lets you describe a search in plain words. It can search, rank, write letters and shortlist,
but it can never submit; that stays your click.

## Which sites are automated

Each site's terms were checked; the app only automates what they allow. Details: [docs/PLATFORM_POLICIES.md](docs/PLATFORM_POLICIES.md).

| Site | Finding jobs | Applying |
|---|---|---|
| Employer job systems (Greenhouse, Lever, Ashby) via [freehire.me](https://freehire.me) | search in the app | automated after you authorize |
| Wellfound, Internshala | import the job open in the app's browser | automated after you authorize (sign in first) |
| LinkedIn, Naukri, others | paste the job | assisted: the app prepares everything, you submit and press *I submitted it* |

The app never solves CAPTCHAs or one-time codes, never hides that it is automated, and never uses proxies.

## Where your data lives

Everything personal stays in `instance/` on your computer and is never committed to git.

| What | Where | Protection |
|---|---|---|
| Accounts, profiles, jobs, applications | `instance/jobapply.db` (SQLite) | passwords hashed with argon2 |
| Groq keys, site logins, application history | in the database and `instance/users/<id>/` | encrypted with `instance/master.key` |
| Resumes, cover letter PDFs, screenshots | `instance/users/<id>/` | readable only through your signed-in account |

Back up the whole `instance/` folder to keep your data. Without `master.key`, the encrypted parts can't be read.

## Safety

- The app only listens on `127.0.0.1`; `run.py` refuses any other address. Don't put it on a network.
- Requests from other sites are refused (host and origin checks, CSRF tokens, strict Content-Security-Policy).
- Keys and passwords are removed from every log line before it's stored.

## For developers

```bash
pip install -r requirements.txt && playwright install chromium
python -m pytest -q          # 157 tests, about 2 minutes
```

| Suite | What it checks |
|---|---|
| `test_core.py`, `test_gates.py`, `test_groq.py` | the logic: states, evidence, answers, ranking, profile merge, uploads, letter checks, Groq errors and schemas |
| `test_web.py` | sign-in, CSRF, local-only access, isolation between people, every page and link, the agent |
| `test_e2e.py` | 27 apply scenarios in headless Chrome against `tests/mock_board.py`: success, CAPTCHA, questions, closed, duplicate, limits, crashes, retries, two people |
| `test_ui.py` | 20 Playwright scenarios that click through the app like a person, including phone layouts |

Tests never touch real sites, accounts or keys: each test gets its own `instance/`, Groq is faked, and the job board
runs on `127.0.0.1`. Architecture and decisions are in [docs/](docs/).

## Licence

MIT. Agent skills in `app/agent/vendor/ai-job-search/` come from
[ai-job-search](https://github.com/ai-job-search-dev/ai-job-search) (MIT); see its `PROVENANCE.json`.
