"""Pure logic: states, evidence, answers, form decisions, ranking, profile merge, uploads, letters, schemas, crypto, events."""
from types import SimpleNamespace
import pytest

from app import answers, crypto, db, documents, events, letters, profile as profiles, resumes
from app.agent import skills, tools
from app.ai import service
from app.automation import evidence, forms, states as S
from app.ranking import DEFAULT_WEIGHTS, Semantic, combine, deterministic
from conftest import make_pdf


def P(data):
    return profiles.Profile.model_validate(data)


# ---------- application states ----------

def test_submitted_is_final():
    a = SimpleNamespace(state=S.SUBMITTED)
    with pytest.raises(S.TransitionError):
        S.move(a, S.QUEUED)


def test_queue_cannot_jump_straight_to_submitted():
    with pytest.raises(S.TransitionError):
        S.move(SimpleNamespace(state=S.QUEUED), S.SUBMITTED)


def test_move_sets_fields():
    a = SimpleNamespace(state=S.SELECTED, blocker="x")
    S.move(a, S.QUEUED, blocker="")
    assert (a.state, a.blocker) == (S.QUEUED, "")


# ---------- evidence ----------

def test_testboard_confirmation_gives_reference():
    v = evidence.verify("testboard", "Application received. Reference TB-1001. Good luck!")
    assert v.confirmed and v.reference == "TB-1001"


def test_generic_thanks_is_not_proof():
    assert not evidence.verify("testboard", "Thanks!").confirmed


def test_unknown_system_is_never_confirmed():
    assert not evidence.verify("someats", "Thank you for applying").confirmed


# ---------- answers and form decisions ----------

@pytest.mark.parametrize("q", ["Expected CTC (LPA)", "Are you authorized to work in India?", "Gender", "Do you require visa sponsorship?",
                               "I certify the above is true", "Do you have any criminal convictions?"])
def test_sensitive_questions(q):
    assert answers.is_sensitive(q)


def test_plain_question_is_not_sensitive():
    assert not answers.is_sensitive("Years of Python experience")


def test_sensitive_question_is_never_sent_to_ai(profile_data):
    asked = []
    ctx = forms.FormContext(profile=P(profile_data), rules=[], resume_path="cv.pdf", ai_answer=lambda q, o: asked.append(q) or "10 LPA")
    assert forms.decide("Expected CTC", None, ctx) == (None, "sensitive: needs your saved answer")
    assert asked == []


def test_saved_rule_answers_sensitive_question(profile_data):
    rule = {"pattern": "ctc", "answer": "As per company norms"}
    ctx = forms.FormContext(profile=P(profile_data), rules=[rule], resume_path="cv.pdf")
    assert forms.decide("Expected CTC", None, ctx) == ("As per company norms", "rule")


def test_identity_comes_from_profile_before_rules(profile_data):
    ctx = forms.FormContext(profile=P(profile_data), rules=[{"pattern": "email", "answer": "wrong@example.com"}], resume_path="cv.pdf")
    assert forms.decide("Email address", None, ctx) == ("asha@example.com", "profile")


def test_ai_answer_outside_options_is_rejected(profile_data):
    ctx = forms.FormContext(profile=P(profile_data), rules=[], resume_path="cv.pdf", ai_answer=lambda q, o: "Maybe")
    assert forms.decide("Are you comfortable with on-call?", ["Yes", "No"], ctx) == (None, "unknown")


def test_cover_letter_question_gets_letter_text(profile_data):
    ctx = forms.FormContext(profile=P(profile_data), rules=[], resume_path="cv.pdf", cover_text="Dear team")
    assert forms.decide("Why do you want to work here?", None, ctx) == ("Dear team", "cover letter")


# ---------- ranking ----------

def test_missing_dimensions_do_not_count_against_a_job(profile_data):
    det = deterministic({"title": "ML Engineer", "description": "Python and PyTorch", "location": "Bengaluru"}, P(profile_data))
    r = combine(det, None, DEFAULT_WEIGHTS)
    assert det["education"][0] is None and r.match_score >= 75


def test_senior_role_scores_low_on_experience(profile_data):
    det = deterministic({"title": "Senior ML Engineer", "description": "5+ years of Python"}, P(profile_data))
    assert det["experience"][0] < 50


def test_semantic_scores_fill_role_dimensions(profile_data):
    sem = Semantic(role_alignment=90, project_relevance=80, industry_relevance=None, strengths=["RAG"], gaps=[],
                   requirements_matched=["Python"], requirements_missing=[], confidence="high")
    r = combine(deterministic({"title": "ML Intern", "description": "Python"}, P(profile_data)), sem, DEFAULT_WEIGHTS)
    assert r.dimensions["role"].score == 90 and r.strengths == ["RAG"]


# ---------- profile ----------

def test_import_never_overwrites_a_verified_value(profile_data):
    parsed = P({**profile_data, "personal": {**profile_data["personal"], "phone": "+1 555"}})
    proposed, changes = profiles.merge(P(profile_data), parsed, "resume:1")
    assert proposed.personal.phone == "+91 90000 00000"
    assert {"path": "personal.phone", "kind": "conflict", "old": "+91 90000 00000", "new": "+1 555"} in changes


def test_accepted_conflict_takes_resume_value(profile_data):
    parsed = P({**profile_data, "personal": {**profile_data["personal"], "phone": "+1 555"}})
    proposed, changes = profiles.merge(P(profile_data), parsed, "resume:1")
    final = profiles.apply_conflicts(proposed, changes, {changes.index(next(c for c in changes if c["kind"] == "conflict"))})
    assert final.personal.phone == "+1 555"


def test_import_adds_new_bullets_with_source(profile_data):
    more = P(profile_data); more.experience[0].bullets.append(profiles.Bullet(id="x", text="Shipped a feature store"))
    proposed, _ = profiles.merge(P(profile_data), more, "resume:7")
    assert proposed.experience[0].bullets[-1].text == "Shipped a feature store"
    assert proposed.experience[0].bullets[-1].source == "resume:7"


def test_empty_profile_is_incomplete():
    pct, missing = profiles.completeness(profiles.Profile())
    assert pct < 20 and missing


def test_save_keeps_versions(profile_data):
    s = db.session(); u = db.User(email="v@example.com", name="V", password_hash="x"); s.add(u); s.flush()
    v1 = profiles.save(s, u.id, P(profile_data), "manual"); v2 = profiles.save(s, u.id, profiles.Profile(), "manual")
    assert (v1, v2) == (1, 2) and profiles.current(s, u.id)[1] == 2 and len(profiles.versions(s, u.id)) == 2
    s.close()


# ---------- uploads ----------

def test_pdf_upload_is_accepted():
    assert resumes.validate("cv.pdf", make_pdf()) == ".pdf"


@pytest.mark.parametrize("name,data,why", [("cv.exe", b"MZ", "PDF or Word"), ("cv.pdf", b"", "empty"), ("cv.pdf", b"<html>", "not a real PDF"),
                                           ("cv.docx", b"PK\x03\x04junk", "Word")])
def test_bad_uploads_are_refused(name, data, why):
    with pytest.raises(resumes.UploadError, match=why):
        resumes.validate(name, data)


def test_oversized_upload_is_refused():
    with pytest.raises(resumes.UploadError):
        resumes.validate("cv.pdf", b"%PDF" + b"0" * (6 * 1024 * 1024))


# ---------- cover letter evidence check ----------

def test_invented_number_is_flagged(profile_data):
    job = SimpleNamespace(title="ML Intern", description="Python")
    issues = letters.check(["I improved accuracy by 73%."], P(profile_data), job)
    assert any("73%" in i for i in issues)


def test_real_numbers_and_plain_words_pass(profile_data):
    job = SimpleNamespace(title="ML Intern", description="Python")
    assert letters.check(["I cut inference latency by 40% with ONNX export."], P(profile_data), job) == []


def test_same_number_written_differently_passes(profile_data):
    job = SimpleNamespace(title="ML Intern", description="Python")
    letter = ["I built a RAG search over 12,000 documents and cut latency by 40 percent; shelf detection reached 92 per cent."]
    assert letters.check(letter, P(profile_data), job) == []


def test_hype_and_em_dash_are_flagged(profile_data):
    issues = letters.check(["I am passionate — truly."], P(profile_data), SimpleNamespace(title="", description=""))
    assert len(issues) == 2


# ---------- tailored CV ----------

def test_tailoring_drops_unknown_bullets_skills_and_numbers(profile_data):
    plan = documents.Plan(profile_line="Delivered 300% growth", competencies=[documents.Competency(label="ML", items="PyTorch, Haskell")],
                          sections=[documents.Section(id="acme", bullet_ids=["acme-2", "made-up"])])
    out = documents.checked(plan, P(profile_data))
    assert out.sections[0].bullet_ids == ["acme-2"]
    assert out.competencies[0].items == "PyTorch"
    assert out.profile_line == "CS student building ML systems"


def test_tailoring_diff_lists_kept_and_dropped(profile_data):
    plan = documents.Plan(profile_line="", competencies=[], sections=[documents.Section(id="acme", bullet_ids=["acme-2"])])
    d = documents.diff(plan, P(profile_data))[0]
    assert d["kept"] == ["Built a RAG search over 12k documents"] and d["dropped"] == ["Cut inference latency by 40% with ONNX export"]


# ---------- Groq schema, crypto, events, skills, tools ----------

@pytest.mark.parametrize("model", [service.Ping, Semantic, letters.Draft, letters.OneParagraph, documents.Plan, profiles.Facts])
def test_every_schema_sent_to_groq_is_strict(model):
    """Groq strict mode rejects any object without additionalProperties false, so free-form maps must never be sent."""
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                assert node.get("additionalProperties") is False, node
                assert set(node.get("required", [])) == set(node.get("properties", {})), node
            for v in node.values(): walk(v)
        elif isinstance(node, list):
            for v in node: walk(v)
    walk(service.strict_schema(model))


def test_strict_schema_requires_every_field():
    sch = service.strict_schema(Semantic)
    assert sch["additionalProperties"] is False and set(sch["required"]) == set(sch["properties"])
    assert "minimum" not in str(sch) and "$defs" not in sch


def test_encryption_round_trip_and_wrong_key(monkeypatch):
    blob = crypto.encrypt_json({"a": 1})
    assert crypto.decrypt_json(blob) == {"a": 1}
    from cryptography.fernet import Fernet
    monkeypatch.setenv("JOBAPPLY_MASTER_KEY", Fernet.generate_key().decode())
    with pytest.raises(RuntimeError):
        crypto.decrypt_json(blob)


def test_events_never_store_keys_or_passwords():
    s = db.session(); u = db.User(email="e@example.com", name="E", password_hash="x"); s.add(u); s.flush()
    ev = events.emit(s, u.id, "using gsk_abcdefghijklmnop1234 and password=hunter2", detail={"note": "token: abc123"})
    assert "gsk_abcdefghijklmnop" not in ev.message and "hunter2" not in ev.message and "abc123" not in ev.detail["note"]
    s.close()


def test_linkedin_skill_is_disabled_for_terms():
    reg = skills.registry()
    assert reg["linkedin-search"].status == "disabled" and reg["freehire-search"].status == "enabled"
    assert skills.provenance()["commit"].startswith("83c788d")


def test_agent_has_no_submit_tool():
    names = {t["name"] for t in tools.schemas()}
    assert "select_jobs" in names and not any("submit" in n or "apply" in n for n in names)


def test_bad_tool_arguments_go_back_to_the_model():
    assert "invalid arguments" in tools.call(None, 1, "rank_jobs", {"job_ids": "nope"}, 0)["error"]
    assert "unknown tool" in tools.call(None, 1, "submit_application", {}, 0)["error"]


# ---------- company job sites (pasted links) ----------

@pytest.mark.parametrize("url,expected", [
    ("https://job-boards.greenhouse.io/enterpret/jobs/7723230003", ("greenhouse", "enterpret", "7723230003")),
    ("https://boards.greenhouse.io/acme/jobs/123?gh_src=x", ("greenhouse", "acme", "123")),
    ("https://job-boards.greenhouse.io/embed/job_app?for=acme&token=456", ("greenhouse", "acme", "456")),
    ("https://jobs.lever.co/leverdemo/ad208490-4052/apply", ("lever", "leverdemo", "ad208490-4052")),
    ("https://jobs.ashbyhq.com/Ashby/7458d4e9/application", ("ashby", "Ashby", "7458d4e9")),
    ("https://www.linkedin.com/jobs/view/4012345678", None), ("https://evil.example/greenhouse.io/acme/jobs/1", None)])
def test_portal_links_are_recognised(url, expected):
    from app.platforms.ats import parse_link
    assert parse_link(url) == expected


def test_greenhouse_posting_text_is_readable():
    from app.platforms.ats import from_posting
    d = from_posting("greenhouse", "enterpret", "1", {"title": "ML Intern", "company_name": "Enterpret", "location": {"name": "Bengaluru"},
                                                     "content": "&lt;h3&gt;About&lt;/h3&gt;&lt;p&gt;Python &amp;amp; RAG&lt;/p&gt;"})
    assert d == {"title": "ML Intern", "company": "Enterpret", "location": "Bengaluru", "description": "About\nPython & RAG"}


def test_ashby_job_missing_from_board_is_reported():
    from app.platforms.ats import from_posting
    with pytest.raises(ValueError, match="no longer"):
        from_posting("ashby", "acme", "gone", {"jobs": [{"id": "other", "title": "x"}]})


def test_pasted_portal_link_becomes_an_automated_job():
    from types import SimpleNamespace as NS
    from app.platforms import apply_adapter
    from app.platforms.manual import Manual
    j = Manual().normalize_job({"url": "https://jobs.lever.co/leverdemo/abc-123", "title": "Engineer", "description": "Python"})
    assert (j["platform"], j["ats"], j["source_id"]) == ("employer", "lever", "leverdemo/abc-123")
    assert apply_adapter(NS(**j)) is not None


# ---------- web search and Auto-apply ----------

def test_web_search_asks_one_query_per_site():
    from app.platforms.websearch import freshness, queries
    assert queries("ML intern", "Bengaluru", ["jobs.lever.co", "greenhouse.io"]) == ['"ML intern" Bengaluru site:jobs.lever.co', '"ML intern" Bengaluru site:greenhouse.io']
    assert queries("ML intern", "India", ["jobs.lever.co"]) == ['"ML intern" site:jobs.lever.co']
    assert [freshness(d) for d in (1, 7, 14, 90)] == ["pd", "pw", "pm", "py"]


@pytest.mark.parametrize("url,kind", [("https://job-boards.greenhouse.io/acme/jobs/1", "employer"), ("https://internshala.com/internship/detail/ml-at-x123", "internshala"),
                                      ("https://in.linkedin.com/jobs/view/4012345678", "linkedin"), ("https://www.naukri.com/job-listings-1", None),
                                      ("https://blog.example/internship/detail/x", None)])
def test_web_results_are_kept_only_for_sites_it_can_apply_to(url, kind):
    from app.platforms.websearch import kind_of
    assert kind_of(url) == kind


def test_auto_apply_runs_on_its_schedule():
    from datetime import datetime, timedelta, timezone
    from app.automation.autopilot import is_due
    now = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)
    assert not is_due({}, now) and not is_due({"on": False}, now)
    assert is_due({"on": True}, now)
    assert not is_due({"on": True, "every_hours": 12, "last_run": (now - timedelta(hours=11)).isoformat()}, now)
    assert is_due({"on": True, "every_hours": 12, "last_run": (now - timedelta(hours=12)).isoformat()}, now)
