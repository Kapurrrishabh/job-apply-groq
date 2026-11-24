"""Every test gets its own instance folder, database and master key, so nothing touches instance/ or .env."""
import json
import re
import pytest
from cryptography.fernet import Fernet

from app import db
from app.ai import service
from app.ai.provider import LLMError, Reply

PROFILE = {
    "personal": {"name": "Asha Rao", "email": "asha@example.com", "phone": "+91 90000 00000", "current_location": "Bengaluru",
                 "linkedin": "https://linkedin.com/in/asha", "github": "https://github.com/asha", "headline": "CS student building ML systems"},
    "education": [{"institution": "Test University", "degree": "B.Tech Computer Science", "start": "2022", "end": "2026", "status": "In progress", "grade": "8.9 CGPA"}],
    "experience": [{"id": "acme", "company": "Acme AI", "title": "ML Intern", "employment_type": "internship", "start": "May 2025", "end": "Jul 2025",
                    "technologies": ["Python", "PyTorch"], "bullets": [{"id": "acme-1", "text": "Cut inference latency by 40% with ONNX export"},
                                                                       {"id": "acme-2", "text": "Built a RAG search over 12k documents"}]}],
    "projects": [{"id": "vision", "name": "Shelf Vision", "start": "2024", "end": "2024", "technologies": ["OpenCV"],
                  "bullets": [{"id": "vision-1", "text": "Detected empty shelves at 92% precision"}]}],
    "skills": {"languages": ["Python", "SQL"], "frameworks": ["PyTorch", "FastAPI"], "ml_ai": ["RAG", "Computer Vision"]},
    "languages": [{"name": "English", "level": "Fluent"}],
    "preferences": {"roles": ["ML Engineer"], "locations": ["Bengaluru", "Remote"], "job_types": ["internship", "full-time"], "max_years_required": 1},
}


@pytest.fixture(autouse=True)
def instance(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBAPPLY_INSTANCE", str(tmp_path / "instance"))
    monkeypatch.setenv("JOBAPPLY_NO_WORKER", "1")
    monkeypatch.setenv("JOBAPPLY_HEADLESS", "1")
    monkeypatch.setenv("JOBAPPLY_MASTER_KEY", Fernet.generate_key().decode())
    db.init()
    yield tmp_path / "instance"
    service.use_provider(None)


@pytest.fixture
def profile_data():
    return json.loads(json.dumps(PROFILE))


class Router:
    """Fake Groq: picks a reply by what the system prompt asks for, and records every call."""
    def __init__(self, routes: dict):
        self.routes, self.calls = routes, []

    def chat(self, messages, **kw):
        self.calls.append({"messages": messages, **kw})
        system = messages[0]["content"]
        for needle, reply in self.routes.items():
            if needle.lower() in system[:160].lower():   # match on the opening sentence only
                r = reply(messages) if callable(reply) else reply
                if isinstance(r, Exception): raise r
                return r if isinstance(r, Reply) else Reply(content=json.dumps(r), prompt_tokens=10, completion_tokens=5)
        raise LLMError("server", f"Router has no reply for: {system[:80]}")


DEFAULT_ROUTES = {
    "connectivity check": {"ok": True},
    "write a cover letter": {"paragraphs": ["I am applying for the ML Intern role as a final-year CS student.",
                                    "At Acme AI I cut inference latency by 40% with ONNX export.", "I can start in January."]},
    "answer one job-application question": {"answer": None, "confident": False},
    "assess how well": {"role_alignment": 85, "project_relevance": 80, "industry_relevance": None, "strengths": ["Built a RAG search"],
                        "gaps": ["No production MLOps"], "requirements_matched": ["Python"], "requirements_missing": [], "confidence": "high"},
}


@pytest.fixture
def groq():
    """Install a fake Groq for this test; returns the Router so tests can add routes and inspect calls."""
    r = Router(dict(DEFAULT_ROUTES))
    service.use_provider(lambda uid: r)
    return r


def make_pdf(text: str = "Asha Rao resume. Python, PyTorch, RAG.") -> bytes:
    from fpdf import FPDF
    pdf = FPDF(); pdf.add_page(); pdf.set_font("Helvetica", size=11); pdf.multi_cell(0, 6, text)
    return bytes(pdf.output())


class Browserless:
    """A signed-in web client that adds the CSRF token to every form post."""
    def __init__(self, client):
        self.c = client

    @property
    def csrf(self) -> str:
        return re.search(r'name="csrf" value="([^"]+)"', self.c.get("/settings").text).group(1)

    def post(self, url, data=None, **kw):
        return self.c.post(url, data={**(data or {}), "csrf": self.csrf}, **kw)

    def get(self, url, **kw):
        return self.c.get(url, **kw)


@pytest.fixture
def web():
    """Factory: web('a@example.com') -> a registered, signed-in client."""
    from fastapi.testclient import TestClient
    from app.web import app
    clients = []

    def make(email="asha@example.com", password="correct horse battery"):
        c = TestClient(app); c.__enter__(); clients.append(c)
        r = c.post("/register", data={"email": email, "name": "Asha Rao", "password": password})
        assert r.status_code == 200, r.text
        return Browserless(c)
    yield make
    for c in clients: c.__exit__(None, None, None)
