"""The web UI through its public surface: sign-in, CSRF, local-only guard, per-user isolation, and every page rendering."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db
from app.web import app
from conftest import make_pdf

PAGES = ["/", "/jobs", "/agent", "/queue", "/applications", "/letters", "/profile", "/resumes", "/answers", "/console", "/skills",
         "/settings", "/diagnostics"]


def test_signed_out_users_are_sent_to_sign_in(web):
    web()   # an account exists, so /login is shown instead of /register
    with TestClient(app) as c:
        r = c.get("/jobs", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_wrong_password_is_refused(web):
    web()
    with TestClient(app) as c:
        r = c.post("/login", data={"email": "asha@example.com", "password": "wrong password!"})
        assert "Wrong email or password" in r.text or "msg=" in str(r.url)
        assert c.get("/jobs", follow_redirects=False).status_code == 303


def test_short_password_is_refused():
    with TestClient(app) as c:
        r = c.post("/register", data={"email": "x@example.com", "name": "X", "password": "short"})
        assert "kind=err" in str(r.url)


def test_post_without_csrf_token_is_rejected(web):
    u = web()
    assert u.c.post("/answers", data={"count": "0"}).status_code == 403


def test_requests_from_other_hosts_are_rejected(web):
    u = web()
    assert u.c.get("/", headers={"host": "evil.example"}).status_code == 403
    assert u.post("/answers", data={"count": "0"}, headers={"origin": "https://evil.example"}).status_code == 403


@pytest.mark.parametrize("path", PAGES)
def test_every_page_renders(web, path):
    r = web().get(path)
    assert r.status_code == 200, r.text[:500]
    assert "Content-Security-Policy" in r.headers and r.headers["X-Frame-Options"] == "DENY"


def test_groq_key_is_never_shown_back(web):
    u = web()
    u.post("/settings", data={"api_key": "gsk_" + "z" * 40, "model": "openai/gpt-oss-120b", "temperature": "0.2", "max_tokens": "2000",
                              "min_score": "60", "daily_limit": "15", "letter_style": "professional"})
    page = u.get("/settings").text
    assert "zzzz" in page and "gsk_zzzzzzzz" not in page


def test_invalid_key_shape_is_refused(web):
    r = web().post("/settings", data={"api_key": "not-a-key", "model": "openai/gpt-oss-120b"})
    assert "kind=err" in str(r.url)


def test_connection_test_uses_the_fake_groq(web, groq):
    r = web().post("/settings/test")
    assert "Groq+connected" in str(r.url) or "Groq connected" in r.text


def test_users_cannot_see_each_others_jobs(web):
    a, b = web("a@example.com"), web("b@example.com")
    r = a.post("/jobs/import", data={"title": "Private Role", "company": "Acme", "description": "Python"})
    jid = int(str(r.url).split("/jobs/")[1].split("?")[0])
    assert b.get(f"/jobs/{jid}").status_code == 404
    assert "Private Role" not in b.get("/jobs").text
    assert b.post("/jobs/bulk", data={"job": str(jid), "action": "select"}).status_code == 404


def test_profile_basics_save_as_a_new_version(web):
    u = web()
    u.post("/profile/basic", data={"name": "Asha Rao", "email": "asha@example.com", "phone": "123", "roles": "ML Engineer, SDE",
                                   "work_modes": ["remote"], "job_types": ["internship"], "max_years": "1", "size_min": "1"})
    page = u.get("/profile").text
    assert "ML Engineer, SDE" in page and "Version 1" in page


def test_bad_yaml_does_not_change_the_profile(web):
    u = web()
    r = u.post("/profile/sections", data={"text": "education: [unclosed"}, follow_redirects=False)
    assert r.headers["location"].startswith("/profile?msg=Not") and r.headers["location"].endswith("#sections")
    assert "Version 0" in u.get("/profile").text


def test_answers_save_and_match(web):
    u = web()
    u.post("/answers", data={"count": "0", "pattern_0": "notice period", "answer_0": "Immediately"})
    assert "Immediately" in u.get("/answers?test=What is your notice period?").text


def test_invalid_answer_pattern_is_refused(web):
    r = web().post("/answers", data={"count": "0", "pattern_0": "(", "answer_0": "x"})
    assert "kind=err" in str(r.url)


def test_resume_upload_becomes_default(web):
    u = web()
    r = u.post("/resumes/upload", files={"file": ("cv.pdf", make_pdf(), "application/pdf")}, data={"label": "Main CV"})
    assert "Uploaded" in r.text and "default" in u.get("/resumes").text


def test_fake_pdf_upload_is_refused(web):
    r = web().post("/resumes/upload", files={"file": ("cv.pdf", b"<script>", "application/pdf")})
    assert "kind=err" in str(r.url)


def test_resume_import_needs_review_before_saving(web, groq, profile_data):
    u = web()
    u.post("/resumes/upload", files={"file": ("cv.pdf", make_pdf(), "application/pdf")})
    groq.routes["extract the candidate"] = profile_data
    rid = db.session().scalar(select(db.Resume.id))
    r = u.post(f"/resumes/{rid}/import")
    assert "/profile/review" in str(r.url) and "Version 0" in u.get("/profile").text
    u.post("/profile/review", data={"action": "apply"})
    assert "Asha Rao" in u.get("/profile").text and "Version 1" in u.get("/profile").text


def test_applying_needs_the_confirmation_tick(web):
    u = web()
    r = u.post("/jobs/import", data={"title": "ML Intern", "company": "Acme", "description": "Python"})
    jid = str(r.url).split("/jobs/")[1].split("?")[0]
    u.post("/jobs/bulk", data={"job": jid, "action": "select"})
    aid = str(db.session().scalar(select(db.Application.id)))
    r = u.post("/queue/apply", data={"app": aid})
    assert "kind=err" in str(r.url)
    assert db.session().scalar(select(db.Application.state)) == "Selected"
    u.post("/queue/apply", data={"app": aid, "confirm": "yes"})
    assert db.session().scalar(select(db.Application.state)) == "Queued"


def test_assisted_platform_is_prepared_not_submitted(web, groq, profile_data):
    from app import profile as profiles
    from app.automation import worker
    u = web(); s = db.session(); uid = s.scalar(select(db.User.id))
    profiles.save(s, uid, profiles.Profile.model_validate(profile_data), "manual"); s.commit(); s.close()
    u.post("/resumes/upload", files={"file": ("cv.pdf", make_pdf(), "application/pdf")})
    r = u.post("/jobs/import", data={"url": "https://www.linkedin.com/jobs/view/1234567890", "title": "ML Intern", "company": "Acme",
                                     "description": "Python"})
    jid = str(r.url).split("/jobs/")[1].split("?")[0]
    u.post("/jobs/bulk", data={"job": jid, "action": "select"})
    u.post("/queue/apply", data={"app": str(db.session().scalar(select(db.Application.id))), "confirm": "yes"})
    worker.run_pending_now(30)
    a = db.session().scalar(select(db.Application))
    assert a.platform == "linkedin" and a.state == "Awaiting User" and "Assisted" in a.blocker
    assert "I submitted it" in u.get("/queue").text


def test_agent_shortlists_but_never_submits(web, groq):
    from app.ai.provider import Reply
    from app.automation import worker
    a, b = web("a@example.com"), web("b@example.com")
    mine = str(a.post("/jobs/import", data={"title": "ML Intern", "company": "Acme", "description": "Python"}).url).split("/jobs/")[1].split("?")[0]
    theirs = str(b.post("/jobs/import", data={"title": "Their Job", "company": "Other", "description": "Go"}).url).split("/jobs/")[1].split("?")[0]
    turns = [Reply(tool_calls=[{"id": "1", "name": "list_jobs", "arguments": {"verdict": "any", "min_score": 0, "limit": 5}}]),
             Reply(tool_calls=[{"id": "2", "name": "select_jobs", "arguments": {"job_ids": [int(mine), int(theirs)]}},
                               {"id": "3", "name": "submit_application", "arguments": {"job_id": int(mine)}}]),
             Reply(content="Shortlisted ML Intern at Acme for your review.")]
    groq.routes["job-search assistant"] = lambda m: turns.pop(0)
    a.post("/agent", data={"prompt": "Shortlist my best job"}); worker.run_pending_now(30)
    s = db.session()
    assert [x.state for x in s.scalars(select(db.Application))] == []   # the batch failed on the other user's id: nothing selected
    log = " ".join(e.message for e in s.scalars(select(db.ExecutionEvent)))
    assert "Job not found" in log and "unknown tool 'submit_application'" in log and "Shortlisted" in log
    turns[:] = [Reply(tool_calls=[{"id": "4", "name": "select_jobs", "arguments": {"job_ids": [int(mine)]}}]), Reply(content="Done.")]
    a.post("/agent", data={"prompt": "Shortlist job " + mine}); worker.run_pending_now(30)
    assert [x.state for x in db.session().scalars(select(db.Application))] == ["Selected"]


def test_database_is_backed_up_before_a_migration(monkeypatch, instance):
    from sqlalchemy import text
    db.init()
    monkeypatch.setattr(db, "MIGRATIONS", [(2, lambda conn: conn.execute(text("CREATE TABLE extra (id INTEGER)")))])
    db.init()
    with db.engine.begin() as conn:
        assert conn.execute(text("PRAGMA user_version")).scalar() == 2
    assert list((instance / "backups").glob("jobapply-v1-*.db"))


def test_every_template_link_and_form_has_a_route():
    """A button pointing at a route that doesn't exist only shows up when someone clicks it."""
    import re
    from pathlib import Path
    from starlette.routing import Match
    templates = Path(__file__).resolve().parent.parent / "app" / "templates"
    broken = []
    for f in templates.glob("*.html"):
        html = f.read_text()
        targets = [("GET" if 'method="get"' in tag else "POST", url) for tag, url in re.findall(r'(<form[^>]*?action="(/[^"#?]*)")', html)]
        targets += [("POST", url) for url in re.findall(r'formaction="(/[^"#?]*)', html)]
        targets += [("GET", url) for url in re.findall(r'(?:href|src)="(/[^"#?]*)', html)]
        for verb, url in targets:
            path = re.sub(r"\{\{.*?\}\}|\{%.*?%\}", "1", url)
            if path.startswith("/static/"): continue
            scope = {"type": "http", "path": path, "method": verb}
            if not any(r.matches(scope)[0] == Match.FULL for r in app.routes): broken.append(f"{f.name}: {verb} {url}")
    assert broken == []


def test_every_label_is_linked_to_its_field():
    """Unlinked labels can't be clicked and aren't read out by screen readers."""
    import re
    from pathlib import Path
    loose = []
    for f in (Path(__file__).resolve().parent.parent / "app" / "templates").glob("*.html"):
        html = f.read_text()
        for m in re.finditer(r"<label(?P<attrs>[^>]*)>(?P<body>.*?)</label>", html, re.S):
            if "for=" not in m["attrs"] and not re.search(r"<(input|select|textarea)", m["body"]): loose.append(f"{f.name}: {m['body'][:40]}")
            target = re.search(r'for="([^"]+)"', m["attrs"])
            if target and f'id="{target[1]}"' not in html[m.end():].split("<label")[0]:
                loose.append(f"{f.name}: '{m['body'][:30]}' is not followed by its field")
        ids = re.findall(r'\bid="([^"{]+)"', html)
        loose += [f"{f.name}: duplicate id {i}" for i in set(ids) if ids.count(i) > 1]
    assert loose == []


def test_test_board_is_hidden_from_real_users(web):
    u = web()
    assert "Test board" not in u.get("/jobs").text
    r = u.post("/jobs/search", data={"platform": "testboard", "query": "intern"})
    assert "kind=err" in str(r.url)


def test_earlier_data_is_imported_into_one_account_only(tmp_path, monkeypatch):
    from app import auth, config, legacy
    monkeypatch.setattr(config, "ROOT", tmp_path)       # never the real data/, .env or browser profile
    (tmp_path / "data").mkdir(); (tmp_path / "data" / "profile.yaml").write_text("identity:\n  name: Old Me\n")
    s = db.session(); u = auth.register(s, "old@example.com", "Old Me", "0123456789"); s.flush()
    assert legacy.available()
    legacy.run(s, u.id)
    assert not legacy.available() and profiles_name(s, u.id) == "Old Me"


def profiles_name(s, uid):
    from app import profile as profiles
    return profiles.current(s, uid)[0].personal.name


def _tex():
    import shutil
    from pathlib import Path
    return shutil.which("lualatex") or Path("/Library/TeX/texbin/lualatex").exists()


@pytest.mark.skipif(not _tex(), reason="needs TeX (lualatex) installed")
def test_tailored_cv_is_one_page_and_waits_for_approval(web, groq, profile_data):
    from pypdf import PdfReader
    from app import profile as profiles, resumes
    from app.automation import worker
    u = web(); s = db.session(); profiles.save(s, 1, profiles.Profile.model_validate(profile_data), "manual"); s.commit(); s.close()
    jid = str(u.post("/jobs/import", data={"title": "ML Intern", "company": "Acme", "description": "PyTorch and RAG"}).url).split("/jobs/")[1].split("?")[0]
    groq.routes["tailor a one-page cv"] = {"profile_line": "CS student building RAG and vision systems.",
                                           "competencies": [{"label": "ML", "items": "PyTorch, RAG"}],
                                           "sections": [{"id": "acme", "bullet_ids": ["acme-2", "acme-1"]}, {"id": "vision", "bullet_ids": ["vision-1"]}]}
    u.post(f"/jobs/{jid}/tailor"); worker.run_pending_now(240)
    s = db.session(); r = resumes.tailored_for(s, 1, int(jid))
    assert r and not r.approved and len(PdfReader(str(resumes.path(r))).pages) == 1
    assert "Built a RAG search over 12k documents" in r.text
    page = u.get(f"/resumes/{r.id}").text
    assert "What changed from your profile" in page and "Approve for applications" in page
    u.post(f"/resumes/{r.id}/approve")
    assert "(tailored)" in u.get(f"/jobs/{jid}").text
