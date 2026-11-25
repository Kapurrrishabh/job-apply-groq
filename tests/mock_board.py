"""A local job board for browser tests: a JSON job API plus real HTML application forms and confirmation pages.
Each job's `kind` drives one scenario: standard, captcha, questions (needs your answers), ambiguous (no confirmation),
noresume (no upload field), broken (server error on submit), choices (radio, checkbox, select), aiq (an everyday question), checkbox (a visible 'I am not a robot' box)."""
from html import escape
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

JOBS = {
    1: {"title": "Machine Learning Intern", "company": "Nimbus Labs", "kind": "standard", "open": True, "description": "Python, PyTorch and RAG. B.Tech students."},
    2: {"title": "Backend Intern", "company": "Closed Co", "kind": "standard", "open": False, "description": "Python and FastAPI."},
    3: {"title": "Data Intern", "company": "Guarded Inc", "kind": "captcha", "open": True, "description": "SQL and Python."},
    4: {"title": "Vision Intern", "company": "Quizzy", "kind": "questions", "open": True, "description": "Computer Vision and OpenCV."},
    5: {"title": "ML Platform Intern", "company": "Vague Systems", "kind": "ambiguous", "open": True, "description": "Python."},
    6: {"title": "Machine Learning Intern", "company": "Nimbus Labs", "kind": "standard", "open": True, "description": "The same role, posted again."},
    7: {"title": "Research Intern", "company": "Paperless", "kind": "noresume", "open": True, "description": "Python."},
    8: {"title": "Cloud Intern", "company": "Flaky Servers", "kind": "broken", "open": True, "description": "Python."},
    9: {"title": "Product Intern", "company": "Choosy", "kind": "choices", "open": True, "description": "Python."},
    10: {"title": "Python Intern", "company": "Asky", "kind": "aiq", "open": True, "description": "Python."},
    11: {"title": "Security Intern", "company": "Checkbox Co", "kind": "checkbox", "open": True, "description": "Python."},
}
SUBMISSIONS: list[dict] = []
app = FastAPI()


def _job(jid: int) -> dict:
    if jid not in JOBS: raise HTTPException(404)
    return {"id": jid, "location": "Bengaluru, India", "type": "Internship", "skills": ["Python"], **JOBS[jid]}


@app.get("/api/jobs")
def jobs(q: str = ""):
    words = q.lower().split()
    return [_job(j) for j in JOBS if not words or any(w in JOBS[j]["title"].lower() for w in words)]


@app.get("/api/jobs/{jid}")
def job(jid: int):
    return _job(jid)


@app.get("/jobs/{jid}", response_class=HTMLResponse)
def job_page(jid: int):
    j = _job(jid)
    return f"<h1>{escape(j['title'])}</h1><p>{escape(j['company'])}</p><p>{escape(j['description'])}</p>"


EXTRA = {
    "questions": """<label for="ds">Describe your favourite data structure *</label><textarea id="ds" name="ds" required></textarea>
<label for="ctc">Expected CTC *</label><select id="ctc" name="ctc" required><option value="">Select</option><option>3-5 LPA</option><option>5-8 LPA</option></select>""",
    "choices": """<fieldset><legend>Are you willing to relocate? *</legend>
<input type="radio" id="ry" name="relocate" value="Yes" required><label for="ry">Yes</label>
<input type="radio" id="rn" name="relocate" value="No"><label for="rn">No</label></fieldset>
<label for="hear">How did you hear about us? *</label><select id="hear" name="hear" required><option value="">Select</option><option>LinkedIn</option><option>Friend</option></select>
<label><input type="checkbox" name="privacy" value="on" required> I agree to the privacy policy</label>""",
    "aiq": """<label for="py">Years of Python experience *</label><input id="py" name="py" required>""",
}


@app.get("/jobs/{jid}/apply", response_class=HTMLResponse)
def form(jid: int):
    j = _job(jid)
    guard = "<p>Please complete the CAPTCHA to continue.</p>" if j["kind"] == "captcha" else ""
    challenge = '<iframe src="/recaptcha/api2/anchor?k=test" width="304" height="78" title="I am not a robot"></iframe>' if j["kind"] == "checkbox" else ""
    return f"""<html><body><h1>Apply: {escape(j['title'])}</h1>{guard}
<form method="post" action="/jobs/{jid}/apply" enctype="multipart/form-data">
<label for="name">Full name *</label><input id="name" name="name" required>
<label for="email">Email *</label><input id="email" name="email" type="email" required>
<label for="phone">Phone *</label><input id="phone" name="phone" required>
{'' if j['kind'] == 'noresume' else '<label for="resume">Resume/CV *</label><input id="resume" name="resume" type="file" required>'}
<label for="why">Why do you want to work here?</label><textarea id="why" name="why"></textarea>
{EXTRA.get(j['kind'], '')}
<button type="submit">Submit application</button></form>
<p>This site is protected by reCAPTCHA and the Google Privacy Policy and Terms of Service apply.</p>
<iframe src="/recaptcha/api2/anchor?size=invisible" width="256" height="60" title="invisible check"></iframe>{challenge}</body></html>"""


@app.post("/jobs/{jid}/apply", response_class=HTMLResponse)
async def submit(jid: int, request: Request):
    j = _job(jid); f = await request.form()
    resume = f.get("resume")
    SUBMISSIONS.append({"job": jid, **{k: v for k, v in f.items() if k != "resume"},
                        "resume_name": getattr(resume, "filename", ""), "resume_bytes": len(await resume.read()) if resume else 0})
    if j["kind"] == "ambiguous": return "<p>Thanks, we will be in touch.</p>"
    if j["kind"] == "broken": return HTMLResponse("<h1>Internal Server Error</h1>", status_code=500)
    return f"<h1>Done</h1><p>Application received. Reference TB-{1000 + len(SUBMISSIONS)}</p>"


# ---------- an Internshala-like site: detail page, apply form with a rich-text box, and "My applications" ----------

INTERNSHALA = {
    "machine-learning-internship-at-deepcraft1234": {"title": "Machine Learning Internship", "company": "DeepCraft", "kind": "open"},
    "data-science-internship-at-oldco5678": {"title": "Data Science Internship", "company": "OldCo", "kind": "applied"},
}


def _intern(slug: str) -> dict:
    if slug not in INTERNSHALA: raise HTTPException(404)
    return INTERNSHALA[slug]


@app.get("/internship/detail/{slug}", response_class=HTMLResponse)
def intern_detail(slug: str):
    j = _intern(slug)
    action = "<p>Already applied</p>" if j["kind"] == "applied" else f"""<button onclick="location.href='/application/form/{slug}'">Apply now</button>"""
    return f"""<html><body><nav>Internships Jobs Courses</nav><h1>{escape(j['title'])}</h1><p>{escape(j['company'])}</p><p>Actively hiring</p>
<p>Work from home</p><p>START DATE</p><p>Immediately</p><p>STIPEND</p><p>₹ 15,000 /month</p>
<h2>About the internship</h2><p>Train computer vision and RAG models in Python and PyTorch.</p>{action}</body></html>"""


@app.get("/application/form/{slug}", response_class=HTMLResponse)
def intern_form(slug: str):
    _intern(slug)
    return f"""<html><body><h1>Applying to {escape(_intern(slug)['company'])}</h1>
<form method="post" action="/application/submit/{slug}" onsubmit="document.getElementById('cl').value = document.querySelector('.ql-editor').innerText">
<h3>Cover letter</h3><p>Why should you be hired for this role?</p>
<div class="ql-editor" contenteditable="true" style="min-height:80px;border:1px solid #999"></div>
<textarea id="cl" name="cover_letter" style="display:none"></textarea>
<div><p>Confirm your availability</p>
<label><input type="radio" name="availability" value="yes" required> Yes, I am available to join immediately</label>
<label><input type="radio" name="availability" value="no"> No (Please specify your availability)</label></div>
<div><p>Have you worked with PyTorch before? Describe briefly.</p><textarea name="q1" required></textarea></div>
<button type="submit">Submit</button></form></body></html>"""


@app.post("/application/submit/{slug}", response_class=HTMLResponse)
async def intern_submit(slug: str, request: Request):
    _intern(slug); f = await request.form()
    SUBMISSIONS.append({"internshala": slug, **dict(f.items())})
    return "<h2>Application submitted successfully!</h2><p>Track it on My applications.</p>"


@app.get("/student/applications", response_class=HTMLResponse)
def intern_applications():
    rows = "".join(f"<div><p>{escape(_intern(x['internshala'])['company'])}</p><p>{escape(_intern(x['internshala'])['title'])}</p><p>Applied</p></div>"
                   for x in SUBMISSIONS if "internshala" in x)
    return f"<html><body><h1>My applications</h1>{rows or '<p>No applications yet</p>'}</body></html>"


# ---------- a LinkedIn-like job page with a multi-step Easy Apply window ----------

LINKEDIN = {"4011111111": {"title": "AI Engineer Intern", "company": "Mock Labs", "kind": "easy"},
            "4022222222": {"title": "Data Analyst", "company": "Elsewhere Inc", "kind": "external"},
            "4033333333": {"title": "ML Engineer", "company": "Been There", "kind": "applied"}}

EASY_APPLY = """<div role="dialog" id="dlg" hidden aria-label="Apply to {company}"><h2>Apply to {company}</h2>
<form method="post" action="/jobs/view/{jid}/easy-apply" enctype="multipart/form-data">
<section data-step="1"><h3>Contact info</h3>
  <label for="fn">First name</label><input id="fn" name="first_name" value="Asha">
  <label for="em">Email address</label><input id="em" name="email" value="asha@example.com">
  <label for="cc">Phone country code</label><select id="cc" name="cc"><option>Select an option</option><option selected value="in">India (+91)</option></select>
  <label for="ph">Mobile phone number</label><input id="ph" name="phone" required>
  <button type="button" onclick="go(2)">Next</button></section>
<section data-step="2" hidden><h3>Resume</h3><label for="up">Upload resume</label>
  <input id="jobs-document-upload-file-input-upload-resume" name="resume" type="file" style="display:none">
  <button type="button" onclick="go(3)">Next</button></section>
<section data-step="3" hidden><h3>Additional questions</h3>
  <label for="py">How many years of work experience do you have with Python?</label><input id="py" name="py" required>
  <label for="au">Are you legally authorized to work in India?</label><select id="au" name="authorized" required><option value="">Select an option</option><option>Yes</option><option>No</option></select>
  <button type="button" onclick="go(4)">Review</button></section>
<section data-step="4" hidden><h3>Review your application</h3><p>Check your details, then submit.</p>
  <button type="submit">Submit application</button></section>
</form></div>
<script>function go(n) {{ document.querySelectorAll('[data-step]').forEach(s => s.hidden = s.dataset.step != n); }}</script>"""


@app.get("/jobs/view/{jid}/", response_class=HTMLResponse)
@app.get("/jobs/view/{jid}", response_class=HTMLResponse)
def linkedin_job(jid: str):
    if jid not in LINKEDIN: raise HTTPException(404)
    j = LINKEDIN[jid]
    button = {"easy": '<button onclick="document.getElementById(\'dlg\').hidden = false">Easy Apply</button>',
              "external": '<a href="https://careers.elsewhere.example/apply">Apply</a>', "applied": "<p>Applied 2 days ago</p>"}[j["kind"]]
    return f"""<html><body><nav>Home My Network Jobs</nav><h1>{escape(j['title'])}</h1><p>{escape(j['company'])}</p><p>Bengaluru, Karnataka, India · Remote</p>
{button}<h2>About the job</h2><p>Build LLM and RAG features in Python. Internship, 6 months.</p>
{EASY_APPLY.format(company=escape(j['company']), jid=jid) if j['kind'] == 'easy' else ''}</body></html>"""


@app.post("/jobs/view/{jid}/easy-apply", response_class=HTMLResponse)
async def linkedin_submit(jid: str, request: Request):
    f = await request.form(); resume = f.get("resume")
    SUBMISSIONS.append({"linkedin": jid, **{k: v for k, v in f.items() if k != "resume"}, "resume_name": getattr(resume, "filename", "")})
    return f"<div role='dialog'><h2>Your application was sent to {escape(LINKEDIN[jid]['company'])}!</h2><button>Done</button></div>"
