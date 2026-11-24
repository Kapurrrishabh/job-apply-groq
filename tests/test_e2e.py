"""End to end with a real headless Chrome against the local mock board (tests/mock_board.py) and a fake Groq.
No real job site, account or API key is touched."""
import pytest
from sqlalchemy import select

import mock_board
from app import db, profile as profiles
from app.ai.provider import LLMError
from app.automation import states as S, worker
from conftest import make_pdf


@pytest.fixture(autouse=True)
def board(board_url, monkeypatch):
    monkeypatch.setenv("JOBAPPLY_TESTBOARD", board_url)
    mock_board.SUBMISSIONS.clear()
    yield mock_board.SUBMISSIONS
    if worker._browser: worker._browser.stop(); worker._browser = None


def jid(title, company):
    return db.session().scalar(select(db.Job.id).where(db.Job.title == title, db.Job.company == company).order_by(db.Job.id))


def app_of(job_id):
    s = db.session(); a = s.scalar(select(db.Application).where(db.Application.job_id == job_id)); s.close(); return a


@pytest.fixture
def ready(web, groq, profile_data):
    """A signed-in user with a profile, a default resume and the board's jobs found."""
    u = web(); s = db.session(); uid = s.scalar(select(db.User.id))
    profiles.save(s, uid, profiles.Profile.model_validate(profile_data), "manual"); s.commit(); s.close()
    u.post("/resumes/upload", files={"file": ("cv.pdf", make_pdf(), "application/pdf")})
    u.post("/jobs/search", data={"platform": "testboard", "query": "intern", "location": "", "days": "30"}); worker.run_pending_now(30)
    return u


def apply(u, *job_ids):
    for j in job_ids: u.post("/jobs/bulk", data={"job": str(j), "action": "select"})
    ids = [str(app_of(j).id) for j in job_ids]
    u.post("/queue/apply", data={"app": ids, "confirm": "yes"}); worker.run_pending_now(120)


def test_full_journey(web, groq, profile_data, board):
    u = web()                                                                                   # 1 account
    u.post("/settings", data={"api_key": "gsk_" + "k" * 40, "model": "openai/gpt-oss-120b", "temperature": "0.2", "max_tokens": "2000",
                              "min_score": "60", "daily_limit": "15", "letter_style": "professional"})   # 2 key
    assert "Groq connected" in u.post("/settings/test").text                                    # 3 connection
    u.post("/resumes/upload", files={"file": ("cv.pdf", make_pdf(), "application/pdf")})        # 4 resume
    groq.routes["extract the candidate"] = profile_data
    rid = db.session().scalar(select(db.Resume.id))
    assert "Review" in u.post(f"/resumes/{rid}/import").text                                    # 5 parse, then review
    u.post("/profile/review", data={"action": "apply"})
    assert "% complete" in u.get("/profile").text                                               # 6 profile saved
    u.post("/answers", data={"count": "0", "pattern_0": "notice period", "answer_0": "Immediately"})   # 7 answers
    u.post("/jobs/search", data={"platform": "testboard", "query": "intern", "location": "", "days": "30"}); worker.run_pending_now(30)
    jobs_page = u.get("/jobs").text
    assert "Nimbus Labs" in jobs_page and "possible duplicate" in jobs_page                    # 8-9 search, dedup marked
    j = jid("Machine Learning Intern", "Nimbus Labs")
    u.post("/jobs/bulk", data={"job": str(j), "action": "rank"}); worker.run_pending_now(30)    # 10 rank
    detail = u.get(f"/jobs/{j}").text
    assert "Built a RAG search" in detail and "automated after you authorize" in detail        # 11 explainable score
    u.post(f"/letters/{j}/generate", data={"style": "internship"}); worker.run_pending_now(30)  # 12 letter
    studio = u.get(f"/letters/{j}").text
    assert "cut inference latency by 40%" in studio
    u.post(f"/letters/{j}/save", data={"count": "3", "p_0": "I am applying for the ML Intern role.", "p_1": "At Acme AI I cut inference latency by 40%.",
                                       "p_2": "I can start in January."})                       # 13 edit
    assert "Version 2" in u.get(f"/letters/{j}").text
    u.post("/jobs/bulk", data={"job": str(j), "action": "select"})                              # 14 shortlist
    assert "Machine Learning Intern" in u.get("/queue").text                                    # 15 queue
    u.post("/queue/apply", data={"app": str(app_of(j).id), "confirm": "yes"}); worker.run_pending_now(120)   # 16 authorize + browser
    a = app_of(j)
    assert a.state == S.SUBMITTED and a.reference.startswith("TB-") and a.evidence["kind"] == "page"        # 17 verified
    sent = board[0]
    assert (sent["name"], sent["email"], sent["phone"]) == ("Asha Rao", "asha@example.com", "+91 90000 00000")
    assert sent["resume_name"].endswith(".pdf") and sent["resume_bytes"] > 500 and "40%" in sent["why"]     # 18 what the board got
    page = u.get(f"/applications/{a.id}").text
    assert a.reference in page and "At Acme AI I cut inference latency by 40%." in page                       # 19 saved preview
    assert u.get(f"/applications/{a.id}/resume").content.startswith(b"%PDF")
    assert u.get(f"/applications/{a.id}/evidence.png").headers["content-type"] == "image/png"
    u.post(f"/applications/{a.id}/delete")                                                                       # 20 delete
    assert app_of(j) is None and "Nimbus Labs" not in u.get("/applications").text


def test_closed_posting_is_blocked(ready, board):
    j = jid("Backend Intern", "Closed Co"); apply(ready, j)
    a = app_of(j)
    assert a.state == S.BLOCKED and "closed" in a.blocker and board == []


def test_captcha_pauses_for_the_user(ready, board):
    j = jid("Data Intern", "Guarded Inc"); apply(ready, j)
    a = app_of(j)
    assert a.state == S.AWAITING and "CAPTCHA" in a.blocker and board == []


def test_visible_robot_check_pauses_for_the_user(ready, board):
    j = jid("Security Intern", "Checkbox Co"); apply(ready, j)
    assert app_of(j).state == S.AWAITING and "CAPTCHA" in app_of(j).blocker and board == []


def test_unknown_questions_wait_for_answers_then_submit(ready, board):
    j = jid("Vision Intern", "Quizzy"); apply(ready, j)
    a = app_of(j)
    assert a.state == S.AWAITING and [q["question"] for q in a.pending] == ["Describe your favourite data structure", "Expected CTC"]
    assert a.pending[1]["sensitive"] and board == []
    ready.post(f"/applications/{a.id}/answer", data={"a_0": "A hash map", "a_1": "3-5 LPA"}); worker.run_pending_now(120)
    a = app_of(j)
    assert a.state == S.SUBMITTED and (board[0]["ds"], board[0]["ctc"]) == ("A hash map", "3-5 LPA")
    assert "A hash map" in ready.get("/answers").text


def test_missing_confirmation_is_unconfirmed_until_you_check(ready, board):
    j = jid("ML Platform Intern", "Vague Systems"); apply(ready, j)
    a = app_of(j)
    assert a.state == S.UNCONFIRMED and len(board) == 1
    ready.post(f"/applications/{a.id}/confirm", data={"note": "Shown in my dashboard"})
    a = app_of(j)
    assert a.state == S.SUBMITTED and a.evidence["kind"] == "user_confirmed"


def test_repost_of_a_submitted_job_is_a_duplicate(ready, board):
    first, again = [r for r in db.session().scalars(select(db.Job.id).where(db.Job.company == "Nimbus Labs").order_by(db.Job.id))]
    apply(ready, first); apply(ready, again)
    assert app_of(first).state == S.SUBMITTED and app_of(again).state == S.DUPLICATE and len(board) == 1


def test_daily_limit_keeps_the_rest_selected(ready, board):
    s = db.session(); s.scalar(select(db.UserSettings)).daily_limit = 1; s.commit(); s.close()
    a, b = jid("Machine Learning Intern", "Nimbus Labs"), jid("ML Platform Intern", "Vague Systems")
    apply(ready, a, b)
    assert app_of(a).state == S.SUBMITTED and app_of(b).state == S.SELECTED and "limit" in app_of(b).blocker


@pytest.mark.parametrize("kind", ["rate_limit", "auth"])
def test_groq_failure_still_applies_without_a_letter(ready, groq, board, kind):
    groq.routes["write a cover letter"] = LLMError(kind, f"Groq failed: {kind}")
    j = jid("Machine Learning Intern", "Nimbus Labs"); apply(ready, j)
    assert app_of(j).state == S.SUBMITTED and board[0]["why"] == ""
    assert "No cover letter" in " ".join(e.message for e in db.session().scalars(select(db.ExecutionEvent)))


def test_restart_recovers_interrupted_work(ready):
    s = db.session()
    a1 = db.Application(user_id=1, job_id=jid("Machine Learning Intern", "Nimbus Labs"), platform="testboard", state=S.SUBMITTING)
    a2 = db.Application(user_id=1, job_id=jid("Data Intern", "Guarded Inc"), platform="testboard", state=S.FILLING)
    ex = db.Execution(user_id=1, kind="apply_batch", status="running", correlation_id="x" * 36)
    s.add_all([a1, a2, ex]); s.commit()
    worker.recover(s); s.refresh(a1); s.refresh(a2); s.refresh(ex)
    assert a1.state == S.UNCONFIRMED and a2.state == S.FAILED and ex.status == "failed"
    s.close()


def events_text():
    return " ".join(e.message for e in db.session().scalars(select(db.ExecutionEvent)))


def test_no_resume_waits_for_an_upload(web, groq, profile_data, board):
    u = web(); s = db.session(); profiles.save(s, 1, profiles.Profile.model_validate(profile_data), "manual"); s.commit(); s.close()
    u.post("/jobs/search", data={"platform": "testboard", "query": "intern", "location": "", "days": "30"}); worker.run_pending_now(30)
    j = jid("Machine Learning Intern", "Nimbus Labs"); apply(u, j)
    assert app_of(j).state == S.AWAITING and "resume" in app_of(j).blocker and board == []


def test_missing_phone_waits_for_the_profile(ready, profile_data, board):
    s = db.session(); p, _ = profiles.current(s, 1); p.personal.phone = ""; profiles.save(s, 1, p, "manual"); s.commit(); s.close()
    j = jid("Machine Learning Intern", "Nimbus Labs"); apply(ready, j)
    assert app_of(j).state == S.AWAITING and "phone" in app_of(j).blocker and board == []


def test_form_without_a_resume_field_is_not_sent(ready, board):
    j = jid("Research Intern", "Paperless"); apply(ready, j)
    assert app_of(j).state == S.AWAITING and "resume upload" in app_of(j).blocker and board == []


def test_server_error_after_submit_is_unconfirmed(ready, board):
    j = jid("Cloud Intern", "Flaky Servers"); apply(ready, j)
    assert app_of(j).state == S.UNCONFIRMED and len(board) == 1


def test_radio_checkbox_and_select_from_saved_answers(ready, board):
    ready.post("/answers", data={"count": "2", "pattern_0": "relocate", "answer_0": "Yes", "pattern_1": "privacy policy", "answer_1": "Yes",
                                 "pattern_2": "hear about us", "answer_2": "LinkedIn"})
    j = jid("Product Intern", "Choosy"); apply(ready, j)
    assert app_of(j).state == S.SUBMITTED
    assert (board[0]["relocate"], board[0]["privacy"], board[0]["hear"]) == ("Yes", "on", "LinkedIn")


def test_consent_checkbox_is_never_ticked_without_your_answer(ready, board):
    j = jid("Product Intern", "Choosy"); apply(ready, j)
    a = app_of(j); pending = {q["question"]: q for q in a.pending}
    assert a.state == S.AWAITING and board == []
    assert pending["I agree to the privacy policy"]["sensitive"] and "Are you willing to relocate?" in pending


def test_confident_ai_answer_fills_an_everyday_question(ready, groq, board):
    groq.routes["answer one job-application question"] = {"answer": "2", "confident": True}
    j = jid("Python Intern", "Asky"); apply(ready, j)
    a = app_of(j)
    assert a.state == S.SUBMITTED and board[0]["py"] == "2"
    assert db.session().scalar(select(db.ApplicationAnswer.source).where(db.ApplicationAnswer.question == "Years of Python experience")) == "ai"


def test_one_click_with_mixed_outcomes(ready, board):
    ok, guard, closed = jid("Machine Learning Intern", "Nimbus Labs"), jid("Data Intern", "Guarded Inc"), jid("Backend Intern", "Closed Co")
    apply(ready, ok, guard, closed)
    assert [app_of(x).state for x in (ok, guard, closed)] == [S.SUBMITTED, S.AWAITING, S.BLOCKED] and len(board) == 1


def test_board_down_fails_then_retry_submits(ready, board, board_url):
    s = db.session(); src = s.get(db.Job, jid("Machine Learning Intern", "Nimbus Labs"))
    dead = db.Job(user_id=1, platform="testboard", source_id="1", url="http://127.0.0.1:9/jobs/1", apply_url="http://127.0.0.1:9/jobs/1/apply",
                  ats="testboard", title="Offline Intern", company="Downtime", description="Python")
    s.delete(src); s.commit(); s.add(dead); s.commit(); j = dead.id; s.close()
    apply(ready, j)
    assert app_of(j).state == S.FAILED and board == []
    s = db.session(); s.get(db.Job, j).apply_url = f"{board_url}/jobs/1/apply"; s.commit(); s.close()
    ready.post(f"/applications/{app_of(j).id}/retry"); worker.run_pending_now(120)
    assert app_of(j).state == S.SUBMITTED and len(board) == 1


def authorize_only(u, *job_ids):
    for j in job_ids: u.post("/jobs/bulk", data={"job": str(j), "action": "select"})
    u.post("/queue/apply", data={"app": [str(app_of(j).id) for j in job_ids], "confirm": "yes"})
    return db.session().scalar(select(db.Execution).where(db.Execution.kind == "apply_batch").order_by(db.Execution.id.desc()))


def test_stopping_a_waiting_run_returns_jobs_to_the_shortlist(ready, board):
    a, b = jid("Machine Learning Intern", "Nimbus Labs"), jid("Vision Intern", "Quizzy")
    ex = authorize_only(ready, a, b)
    ready.post(f"/executions/{ex.id}/cancel"); worker.run_pending_now(30)
    assert [app_of(x).state for x in (a, b)] == [S.SELECTED, S.SELECTED] and board == []


def test_stopping_a_running_batch_returns_the_rest(ready, board):
    a, b = jid("Machine Learning Intern", "Nimbus Labs"), jid("Vision Intern", "Quizzy")
    ex = authorize_only(ready, a, b); worker._cancel.add(ex.id); worker.run_pending_now(30)
    assert [app_of(x).state for x in (a, b)] == [S.SELECTED, S.SELECTED] and board == []
    assert db.session().get(db.Execution, ex.id).status == "cancelled"


def test_crashed_batch_returns_jobs_and_reports_it(ready, board, monkeypatch):
    from app.automation import tasks
    monkeypatch.setattr(tasks, "browser_for", lambda uid: (_ for _ in ()).throw(RuntimeError("Chrome would not start")))
    j = jid("Machine Learning Intern", "Nimbus Labs"); ex = authorize_only(ready, j); worker.run_pending_now(30)
    assert app_of(j).state == S.SELECTED and db.session().get(db.Execution, ex.id).status == "failed"
    assert "Chrome would not start" in events_text()


def test_application_locked_by_another_run_is_left_alone(ready, board):
    from datetime import timedelta
    j = jid("Machine Learning Intern", "Nimbus Labs"); authorize_only(ready, j)
    s = db.session(); a = s.scalar(select(db.Application)); a.lock_owner, a.lock_expires = "someone-else", db.now() + timedelta(minutes=5); s.commit(); s.close()
    worker.run_pending_now(30)
    assert board == [] and "another run holds" in events_text()


def test_wellfound_waits_until_you_sign_in(ready, board, monkeypatch):
    from app.platforms.wellfound import Wellfound
    monkeypatch.setattr(Wellfound, "check_availability", lambda self, b, job: None)
    s = db.session(); w = db.Job(user_id=1, platform="wellfound", source_id="42", url="https://wellfound.com/jobs/42", title="AI Intern",
                                 company="Startup", description="Python"); s.add(w); s.commit(); j = w.id; s.close()
    apply(ready, j)
    assert app_of(j).state == S.AWAITING and "Sign in to Wellfound" in app_of(j).blocker


def test_approved_tailored_cv_is_sent_instead_of_the_default(ready, board):
    from app import resumes
    j = jid("Machine Learning Intern", "Nimbus Labs"); s = db.session()
    pending = resumes.add(s, 1, "t1.pdf", make_pdf("Tailored draft"), "Draft", kind="tailored", job_id=j, approved=False); s.commit()
    apply(ready, j)
    default = resumes.default(s, 1)
    assert board[0]["resume_name"] == default.filename != pending.filename
    other = jid("Python Intern", "Asky")
    approved = resumes.add(s, 1, "t2.pdf", make_pdf("Tailored for Asky"), "For Asky", kind="tailored", job_id=other, approved=True); s.commit()
    s.close()
    ready.post("/answers", data={"count": "0", "pattern_0": "years of python", "answer_0": "2"})
    apply(ready, other)
    assert board[1]["resume_name"] == approved.filename


def test_two_people_apply_with_their_own_details(web, groq, profile_data, board):
    people = []
    for n, (email, name) in enumerate([("a@example.com", "Asha Rao"), ("b@example.com", "Ben Das")], start=1):
        u = web(email); s = db.session()
        data = {**profile_data, "personal": {**profile_data["personal"], "name": name, "email": email}}
        profiles.save(s, n, profiles.Profile.model_validate(data), "manual"); s.commit(); s.close()
        u.post("/resumes/upload", files={"file": ("cv.pdf", make_pdf(f"{name} resume"), "application/pdf")})
        u.post("/jobs/search", data={"platform": "testboard", "query": "intern", "location": "", "days": "30"}); worker.run_pending_now(30)
        people.append(u)
    for n, u in enumerate(people, start=1):
        j = db.session().scalar(select(db.Job.id).where(db.Job.user_id == n, db.Job.company == "Nimbus Labs").order_by(db.Job.id))
        u.post("/jobs/bulk", data={"job": str(j), "action": "select"})
        aid = db.session().scalar(select(db.Application.id).where(db.Application.job_id == j))
        u.post("/queue/apply", data={"app": str(aid), "confirm": "yes"}); worker.run_pending_now(120)
    assert [(x["name"], x["email"]) for x in board] == [("Asha Rao", "a@example.com"), ("Ben Das", "b@example.com")]
    assert board[0]["resume_name"] != board[1]["resume_name"]


# ---------- Internshala (an Internshala-like site on the mock board) ----------

INTERN = "machine-learning-internship-at-deepcraft1234"


def signed_in_to_internshala():
    from app import site_sessions
    s = db.session()
    site_sessions.save(s, 1, "internshala", [{"name": "PHPSESSID", "value": "test", "domain": ".internshala.com", "path": "/", "expires": -1}],
                       domain="internshala.com")
    s.commit(); s.close()


def import_internshala(u, board_url, slug, path="/internship/detail/"):
    s = db.session(); worker.enqueue(s, 1, "open_url", {"url": f"{board_url}{path}{slug}"}); s.commit(); s.close()
    u.post("/jobs/import-page", data={"platform": "internshala"}); worker.run_pending_now(60)
    return db.session().scalar(select(db.Job).where(db.Job.platform == "internshala", db.Job.source_id == slug))


def test_internshala_import_and_apply(ready, board, board_url):
    signed_in_to_internshala()
    job = import_internshala(ready, board_url, INTERN)
    assert (job.title, job.company, job.location, job.salary_text, job.work_mode) == (
        "Machine Learning Internship", "DeepCraft", "Work from home", "₹ 15,000 /month", "remote")
    ready.post("/answers", data={"count": "1", "pattern_0": "availability", "answer_0": "Yes, I am available to join immediately",
                                 "pattern_1": "pytorch before", "answer_1": "Yes: at Acme AI I cut inference latency by 40% with ONNX export."})
    apply(ready, job.id)
    a = app_of(job.id)
    assert a.state == S.SUBMITTED and "applications list shows DeepCraft" in a.evidence["detail"]
    sent = board[-1]
    assert sent["availability"] == "yes" and "40%" in sent["q1"] and "cut inference latency by 40%" in sent["cover_letter"]


def test_internshala_waits_until_you_sign_in(ready, board, board_url):
    job = import_internshala(ready, board_url, INTERN); apply(ready, job.id)
    assert app_of(job.id).state == S.AWAITING and "Sign in to Internshala" in app_of(job.id).blocker and board == []


def test_internshala_questions_wait_for_you_then_submit(ready, board, board_url):
    signed_in_to_internshala()
    job = import_internshala(ready, board_url, INTERN); apply(ready, job.id)
    a = app_of(job.id)
    assert a.state == S.AWAITING and board == []
    assert [q["question"] for q in a.pending] == ["Have you worked with PyTorch before? Describe briefly.", "Confirm your availability"]
    ready.post(f"/applications/{a.id}/answer", data={"a_0": "Yes, at Acme AI.", "a_1": "Yes, I am available to join immediately"})
    worker.run_pending_now(120)
    assert app_of(job.id).state == S.SUBMITTED and board[-1]["q1"] == "Yes, at Acme AI."


def test_internshala_already_applied_sends_nothing(ready, board, board_url):
    signed_in_to_internshala()
    job = import_internshala(ready, board_url, "data-science-internship-at-oldco5678"); apply(ready, job.id)
    a = app_of(job.id)
    assert a.state == S.FAILED and "already applied" in a.error and board == []


def test_internshala_import_refuses_other_pages(ready, board_url):
    assert import_internshala(ready, board_url, "1", path="/jobs/") is None
    assert "Open an Internshala internship or job page" in events_text()
