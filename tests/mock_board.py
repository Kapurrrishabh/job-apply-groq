"""A local job board for browser tests: a JSON job API plus real HTML application forms and confirmation pages.
Each job's `kind` drives one scenario: standard, captcha, questions (needs your answers), ambiguous (no confirmation)."""
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
}


@app.get("/jobs/{jid}/apply", response_class=HTMLResponse)
def form(jid: int):
    j = _job(jid)
    guard = "<p>Please complete the CAPTCHA to continue.</p>" if j["kind"] == "captcha" else ""
    return f"""<html><body><h1>Apply: {escape(j['title'])}</h1>{guard}
<form method="post" action="/jobs/{jid}/apply" enctype="multipart/form-data">
<label for="name">Full name *</label><input id="name" name="name" required>
<label for="email">Email *</label><input id="email" name="email" type="email" required>
<label for="phone">Phone *</label><input id="phone" name="phone" required>
<label for="resume">Resume/CV *</label><input id="resume" name="resume" type="file" required>
<label for="why">Why do you want to work here?</label><textarea id="why" name="why"></textarea>
{EXTRA.get(j['kind'], '')}
<button type="submit">Submit application</button></form></body></html>"""


@app.post("/jobs/{jid}/apply", response_class=HTMLResponse)
async def submit(jid: int, request: Request):
    j = _job(jid); f = await request.form()
    resume = f.get("resume")
    SUBMISSIONS.append({"job": jid, **{k: v for k, v in f.items() if k != "resume"},
                        "resume_name": getattr(resume, "filename", ""), "resume_bytes": len(await resume.read()) if resume else 0})
    if j["kind"] == "ambiguous": return "<p>Thanks, we will be in touch.</p>"
    return f"<h1>Done</h1><p>Application received. Reference TB-{1000 + len(SUBMISSIONS)}</p>"
