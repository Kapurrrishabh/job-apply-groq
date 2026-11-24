"""End to end with a real headless Chrome against the local mock board (tests/mock_board.py) and a fake Groq.
No real job site, account or API key is touched."""
import socket
import threading
import time
import pytest
import uvicorn
from sqlalchemy import select

import mock_board
from app import db, profile as profiles
from app.ai.provider import LLMError
from app.automation import states as S, worker
from conftest import make_pdf


@pytest.fixture(scope="module")
def board_url():
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
    srv = uvicorn.Server(uvicorn.Config(mock_board.app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True); t.start()
    for _ in range(100):
        if srv.started: break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True; t.join(5)


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


def test_groq_rate_limit_still_applies_without_a_letter(ready, groq, board):
    groq.routes["write a cover letter"] = LLMError("rate_limit", "Groq returned 429 after 5 tries")
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
