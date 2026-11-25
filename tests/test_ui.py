"""The app driven like a person would: a real headless Chrome clicking through the UI served on 127.0.0.1.
The background worker runs in its own thread (it owns the app's browser); Groq is faked; jobs come from the mock board."""
import re
from concurrent.futures import ThreadPoolExecutor
import pytest
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import select

import mock_board
from app import db
from app.automation import states as S, worker
from conftest import make_pdf, serve

MAIN = ["Home", "Find jobs", "Shortlist", "Applications", "Cover letters", "AI agent"]
ACCOUNT = ["My profile", "Resumes", "Saved answers", "Settings & job sites", "Activity console", "Agent skills", "Diagnostics"]


def go(page, name):
    """Open a section from the header: the main sections, or the account menu for the rest."""
    if name in MAIN:
        page.locator("nav.main").get_by_role("link", name=name, exact=False).first.click()
    else:
        page.get_by_label("Account menu").click(); page.locator(".menu-list").get_by_role("link", name=name).click()


@pytest.fixture
def app_url(instance):
    from app.web import app
    url, srv, t = serve(app)
    yield url
    srv.should_exit = True; t.join(5)


@pytest.fixture
def board(board_url, monkeypatch):
    monkeypatch.setenv("JOBAPPLY_TESTBOARD", board_url)
    mock_board.SUBMISSIONS.clear()
    return mock_board.SUBMISSIONS


@pytest.fixture
def drain():
    """Run queued work on one dedicated thread, so the app's browser always lives on the thread that created it."""
    pool = ThreadPoolExecutor(max_workers=1)
    yield lambda: pool.submit(worker.run_pending_now, 120).result()

    def stop():
        if worker._browser: worker._browser.stop(); worker._browser = None
    pool.submit(stop).result(); pool.shutdown()


@pytest.fixture
def page():
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception:
            browser = p.chromium.launch(headless=True)
        pg = browser.new_page()
        pg.problems = []
        pg.on("console", lambda m: pg.problems.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: pg.problems.append(str(e)))
        yield pg
        browser.close()


def sign_up(page, app_url, email="asha@example.com", name="Asha Rao"):
    page.goto(f"{app_url}/register")
    page.get_by_label("Your name").fill(name); page.get_by_label("Email").fill(email)
    page.get_by_label("Password").fill("correct horse battery"); page.get_by_role("button", name="Create account").click()
    expect(page.get_by_role("heading", level=1)).to_contain_text(f"Hi {name.split()[0]}")


@pytest.fixture
def me(page, app_url, groq, profile_data, board):
    """Signed in, with a profile and a resume, and the board's jobs found."""
    sign_up(page, app_url)
    from app import profile as profiles
    s = db.session(); profiles.save(s, 1, profiles.Profile.model_validate(profile_data), "manual"); s.commit(); s.close()
    page.goto(f"{app_url}/resumes")
    page.locator("input[name=file]").set_input_files(files=[{"name": "cv.pdf", "mimeType": "application/pdf", "buffer": make_pdf()}])
    page.get_by_role("button", name="Upload").click()
    expect(page.locator(".flash.ok")).to_contain_text("Uploaded")
    return page


def search_board(page, app_url, drain):
    page.goto(f"{app_url}/jobs")
    form = page.locator("form[action='/jobs/search']")
    form.get_by_label("Roles").fill("intern"); form.get_by_label("Location").fill("")
    form.get_by_label("Source").select_option(label="Test board (local)")
    form.get_by_role("button", name="Search", exact=True).click()
    expect(page).to_have_url(re.compile("/console"))
    drain()
    expect(page.locator("#console")).to_contain_text("jobs found", timeout=10000)   # the live console polls /api/events


def shortlist(page, app_url, title):
    page.goto(f"{app_url}/jobs")
    page.locator(".job-card", has_text=title).first.locator("input[type=checkbox][name=job]").check()
    page.get_by_role("button", name="Shortlist selected").click()
    expect(page).to_have_url(re.compile("/queue"))


def authorize(page):
    page.get_by_label(re.compile("I have checked these jobs")).check()
    page.get_by_role("button", name="Apply to selected").click()
    expect(page).to_have_url(re.compile("/console"))


def state_of(title):
    return db.session().scalar(select(db.Application.state).join(db.Job, db.Job.id == db.Application.job_id).where(db.Job.title == title))


# ---------- accounts ----------

def test_sign_up_out_and_back_in(page, app_url):
    page.goto(app_url)
    expect(page).to_have_url(re.compile("/register"))       # the first visit creates the first account
    sign_up(page, app_url)
    page.get_by_label("Account menu").click(); page.get_by_role("button", name="Sign out").click()
    expect(page).to_have_url(re.compile("/login"))
    page.get_by_label("Email").fill("asha@example.com"); page.get_by_label("Password").fill("not the password")
    page.get_by_role("button", name="Sign in").click()
    expect(page.locator(".flash.err")).to_contain_text("Email or password is wrong")
    page.get_by_label("Email").fill("asha@example.com"); page.get_by_label("Password").fill("correct horse battery")
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_role("heading", level=1)).to_contain_text("Hi Asha")


def test_deep_link_returns_after_sign_in(page, app_url):
    sign_up(page, app_url); page.context.clear_cookies()
    page.goto(f"{app_url}/answers")
    page.get_by_label("Email").fill("asha@example.com"); page.get_by_label("Password").fill("correct horse battery")
    page.get_by_role("button", name="Sign in").click()
    expect(page).to_have_url(re.compile("/answers$"))


# ---------- every page, as a person sees it ----------

def test_every_page_opens_from_the_menu_without_errors(page, app_url):
    sign_up(page, app_url)
    for name in MAIN + ACCOUNT:
        go(page, name)
        expect(page.get_by_role("heading", level=1)).to_be_visible()
    assert page.problems == []      # includes Content-Security-Policy violations


@pytest.mark.parametrize("path", ["/", "/jobs", "/queue", "/settings", "/profile"])
def test_pages_fit_a_phone_screen(page, app_url, path):
    sign_up(page, app_url); page.set_viewport_size({"width": 390, "height": 844}); page.goto(f"{app_url}{path}")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


# ---------- the whole journey by clicking ----------

def test_whole_journey_by_clicking(page, app_url, groq, profile_data, board, drain):
    sign_up(page, app_url)
    go(page, "Settings & job sites")
    page.locator("input[name=api_key]").fill("gsk_" + "k" * 40); page.get_by_role("button", name="Save settings").click()
    expect(page.locator(".flash.ok")).to_contain_text("Settings saved")
    expect(page.get_by_text("ends in …kkkk")).to_be_visible()
    page.get_by_role("button", name="Test Groq connection").click()
    expect(page.locator(".flash.ok")).to_contain_text("Groq connected")

    groq.routes["extract the candidate"] = profile_data
    page.goto(f"{app_url}/resumes")
    page.locator("input[name=file]").set_input_files(files=[{"name": "cv.pdf", "mimeType": "application/pdf", "buffer": make_pdf()}])
    page.get_by_role("button", name="Upload").click()
    page.get_by_role("button", name="Update profile from this resume").click()
    expect(page.get_by_role("heading", level=1)).to_contain_text("Review what the resume adds")
    page.get_by_role("button", name="Save to my profile").click()
    expect(page.locator(".flash.ok")).to_contain_text("Imported into version 1")
    expect(page.locator("input[name=name]")).to_have_value("Asha Rao")

    page.goto(f"{app_url}/answers")
    page.locator("input[name=pattern_0]").fill("notice period"); page.locator("input[name=answer_0]").fill("Immediately")
    page.get_by_role("button", name="Save answers").click()
    expect(page.locator(".flash.ok")).to_contain_text("Saved 1 answers")

    search_board(page, app_url, drain)
    page.goto(f"{app_url}/jobs")
    page.locator("[data-select-all=job]").check()
    boxes = page.locator("input[type=checkbox][name=job]")
    assert page.locator("input[type=checkbox][name=job]:checked").count() == boxes.count() > 5
    page.locator("[data-select-all=job]").uncheck()
    page.locator(".job-card", has_text="Nimbus Labs").first.locator("input[type=checkbox][name=job]").check()
    page.get_by_role("button", name="Rank selected").click(); drain()
    page.goto(f"{app_url}/jobs?sort=score")
    page.locator(".job-card", has_text="Nimbus Labs").first.locator(".jc-title").click()
    expect(page.get_by_text("Built a RAG search")).to_be_visible()

    page.get_by_role("link", name="Write cover letter").click()
    page.get_by_role("button", name="Write with Groq").click(); drain(); page.reload()
    expect(page.locator("textarea.para").nth(1)).to_contain_text("40%")
    page.locator("textarea.para").nth(2).fill("I can start in January and work full time.")
    page.get_by_role("button", name="Save my edits").click()
    expect(page.get_by_role("heading", name=re.compile("Version 2"))).to_be_visible()
    groq.routes["write a cover letter"] = lambda m: ({"paragraph": "At Acme AI I built a RAG search over 12k documents."}
                                                     if "Rewrite paragraph" in m[-1]["content"] else {"paragraphs": ["x"]})
    page.locator("select[name=index]").select_option(index=1); page.get_by_role("button", name="Rewrite").click()
    expect(page.locator(".flash.ok")).to_contain_text("Paragraph 2 rewritten (version 3)")

    page.goto(f"{app_url}/jobs?sort=score")
    page.locator(".job-card", has_text="Nimbus Labs").first.locator(".jc-title").click()
    page.get_by_role("button", name="Shortlist to apply").click()
    before = page.url
    page.get_by_role("button", name="Apply to selected").click()          # the confirmation box is required
    expect(page).to_have_url(before)
    assert state_of("Machine Learning Intern") == S.SELECTED
    authorize(page); drain()
    expect(page.locator("#console")).to_contain_text("Submitted", timeout=10000)

    go(page, "Applications")
    page.get_by_role("link", name="Machine Learning Intern").click()
    expect(page.get_by_text(re.compile(r"reference TB-\d+"))).to_be_visible()
    expect(page.locator(".desc")).to_contain_text("At Acme AI I built a RAG search over 12k documents.")
    assert page.locator("img.shot").evaluate("img => img.complete && img.naturalWidth > 0")
    href = page.get_by_role("link", name="download the exact file").get_attribute("href")
    assert page.request.get(f"{app_url}{href}").body().startswith(b"%PDF")
    assert board[0]["why"].count("12k documents") == 1

    page.once("dialog", lambda d: d.dismiss()); page.get_by_role("button", name="Delete this record").click()
    expect(page.get_by_role("heading", level=1)).to_contain_text("Machine Learning Intern")
    page.once("dialog", lambda d: d.accept()); page.get_by_role("button", name="Delete this record").click()
    expect(page.locator(".flash.ok")).to_contain_text("deleted")
    assert state_of("Machine Learning Intern") is None
    assert page.problems == []


# ---------- scenarios that need you ----------

def test_answering_questions_from_the_queue(me, app_url, drain, board):
    search_board(me, app_url, drain); shortlist(me, app_url, "Quizzy"); authorize(me); drain()
    me.goto(f"{app_url}/queue")
    card = me.locator(".card.wait", has_text="Vision Intern")
    expect(card).to_contain_text("2 question(s) need your answer")
    expect(card.locator(".badge", has_text="sensitive")).to_be_visible()
    card.locator("input[name=a_0]").fill("A hash map"); card.locator("select[name=a_1]").select_option("3-5 LPA")
    card.get_by_role("button", name="Save answers and continue").click(); drain()
    assert state_of("Vision Intern") == S.SUBMITTED and board[0]["ds"] == "A hash map"
    me.goto(f"{app_url}/answers")
    expect(me.locator("input[value='A hash map']")).to_be_visible()


def test_captcha_is_handed_to_you(me, app_url, drain, board):
    search_board(me, app_url, drain); shortlist(me, app_url, "Guarded Inc"); authorize(me); drain()
    me.goto(f"{app_url}/queue")
    expect(me.locator(".card.wait", has_text="Data Intern")).to_contain_text("CAPTCHA")
    expect(me.locator("nav.main .pill")).to_have_text("1")
    assert board == []


def test_unconfirmed_submission_until_you_check(me, app_url, drain, board):
    search_board(me, app_url, drain); shortlist(me, app_url, "Vague Systems"); authorize(me); drain()
    me.goto(f"{app_url}/queue")
    card = me.locator(".card.wait", has_text="ML Platform Intern")
    expect(card).to_contain_text("No confirmation was found")
    me.once("dialog", lambda d: d.accept()); card.get_by_role("button", name="I submitted it").click()
    expect(me.locator(".flash.ok")).to_contain_text("confirmed by you")
    assert state_of("ML Platform Intern") == S.SUBMITTED


def test_assisted_site_is_prepared_and_you_submit(me, app_url, drain, board):
    me.goto(f"{app_url}/jobs")
    me.get_by_text("Paste a job from any site").click()
    form = me.locator("form[action='/jobs/import']")
    form.get_by_label("Job link").fill("https://www.linkedin.com/jobs/view/4012345678")
    form.get_by_label("Title").fill("AI Engineer Intern"); form.get_by_label("Company").fill("Linked Co")
    form.get_by_label("Description").fill("Python and LLMs."); form.get_by_role("button", name="Import").click()
    expect(me.get_by_text("assisted: you submit")).to_be_visible()
    me.get_by_role("button", name="Shortlist to apply").click(); authorize(me); drain()
    me.goto(f"{app_url}/queue")
    card = me.locator(".card.wait", has_text="AI Engineer Intern")
    expect(card).to_contain_text("Assisted")
    expect(card.get_by_role("link", name="Cover letter PDF")).to_be_visible()
    me.once("dialog", lambda d: d.accept()); card.get_by_role("button", name="I submitted it").click()
    assert state_of("AI Engineer Intern") == S.SUBMITTED and board == []


def test_removing_a_job_from_the_queue(me, app_url, drain):
    search_board(me, app_url, drain); shortlist(me, app_url, "Nimbus Labs")
    me.locator(".app-card", has_text="Machine Learning Intern").get_by_role("button", name="Remove").click()
    expect(me.locator(".flash.ok")).to_contain_text("Removed from the queue")
    assert state_of("Machine Learning Intern") is None


def test_stop_button_cancels_a_waiting_run(me, app_url):
    me.goto(f"{app_url}/settings")
    me.locator("tr", has_text="LinkedIn").get_by_role("button", name="Sign in").click()   # queued, never run: no real site is opened
    expect(me).to_have_url(re.compile("/console"))
    me.locator("tr", has_text="login").get_by_role("button", name="Stop").click()
    expect(me.locator("tr", has_text="login")).to_contain_text("cancelled")


def test_console_filter_shows_one_level(me, app_url, drain):
    search_board(me, app_url, drain)
    me.locator("#console-filter").select_option("success")
    shown = me.locator("#console .ev:not([hidden])")
    expect(shown.first).to_be_visible()
    assert all(c == "ev ev-success" for c in shown.evaluate_all("els => els.map(e => e.className)"))


def test_agent_run_shows_in_the_console(me, app_url, groq, drain):
    from app.ai.provider import Reply
    turns = [Reply(tool_calls=[{"id": "1", "name": "profile_summary", "arguments": {}}]), Reply(content="Your profile is complete.")]
    groq.routes["job-search assistant"] = lambda m: turns.pop(0)
    me.goto(f"{app_url}/agent"); me.locator("textarea[name=prompt]").fill("How complete is my profile?")
    me.get_by_role("button", name="Run agent").click(); drain()
    expect(me.locator("#console")).to_contain_text("Your profile is complete.", timeout=10000)
    expect(me.locator("#console")).to_contain_text("profile_summary")


def test_bad_inputs_show_clear_messages(me, app_url):
    me.goto(f"{app_url}/resumes")
    me.locator("input[name=file]").set_input_files(files=[{"name": "cv.pdf", "mimeType": "application/pdf", "buffer": b"<html>"}])
    me.get_by_role("button", name="Upload").click()
    expect(me.locator(".flash.err")).to_contain_text("not a real PDF")
    me.goto(f"{app_url}/settings"); me.locator("input[name=api_key]").fill("sk-wrong"); me.get_by_role("button", name="Save settings").click()
    expect(me.locator(".flash.err")).to_be_visible()
    me.goto(f"{app_url}/profile"); me.locator("textarea[name=text]").fill("education: [broken")
    me.get_by_role("button", name="Save sections").click()
    expect(me.locator(".flash.err")).to_contain_text("Not saved")
    me.goto(f"{app_url}/answers"); me.locator("input[name=pattern_0]").fill("("); me.locator("input[name=answer_0]").fill("x")
    me.get_by_role("button", name="Save answers").click()
    expect(me.locator(".flash.err")).to_contain_text("not a valid pattern")


def test_second_person_sees_only_their_own_data(me, app_url, drain, page, board):
    search_board(me, app_url, drain)
    me.context.clear_cookies(); sign_up(me, app_url, "ben@example.com", "Ben Das")
    me.goto(f"{app_url}/jobs")
    expect(me.get_by_text("No jobs yet")).to_be_visible()
    assert me.goto(f"{app_url}/jobs/1").status == 404


def test_waiting_cards_fit_a_phone_screen(me, app_url, drain):
    search_board(me, app_url, drain); shortlist(me, app_url, "Guarded Inc"); authorize(me); drain()
    me.set_viewport_size({"width": 390, "height": 844}); me.goto(f"{app_url}/queue")
    expect(me.locator(".card.wait")).to_be_visible()
    cut_off = me.evaluate("() => [...document.querySelectorAll('.btn')].filter(b => b.getBoundingClientRect().right > window.innerWidth).map(b => b.innerText)")
    assert cut_off == []
