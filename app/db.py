"""SQLite database: models, engine, and a small migration runner (backup first, then PRAGMA user_version steps)."""
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, LargeBinary, String, Text,
                        UniqueConstraint, create_engine, event, text)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from . import config


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProfileVersion(Base):
    __tablename__ = "profile_versions"
    __table_args__ = (UniqueConstraint("user_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(20))       # manual | import | merge | legacy
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Resume(Base):
    __tablename__ = "resumes"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(200))
    filename: Mapped[str] = mapped_column(String(300))     # stored file name under the user's resumes folder
    mime: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text, default="")
    parsed: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    kind: Mapped[str] = mapped_column(String(20), default="uploaded")   # uploaded | tailored
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    approved: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AnswerRule(Base):
    __tablename__ = "answer_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    pattern: Mapped[str] = mapped_column(String(500))
    answer: Mapped[str] = mapped_column(Text)
    note: Mapped[str] = mapped_column(Text, default="")
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False)   # legal/salary/demographic: never guessed


class UserSettings(Base):
    __tablename__ = "user_settings"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    model: Mapped[str] = mapped_column(String(100), default=config.DEFAULT_MODEL)
    temperature: Mapped[float] = mapped_column(Float, default=0.2)
    max_tokens: Mapped[int] = mapped_column(Integer, default=config.DEFAULT_MAX_TOKENS)
    weights: Mapped[dict] = mapped_column(JSON, default=dict)
    min_score: Mapped[int] = mapped_column(Integer, default=60)
    daily_limit: Mapped[int] = mapped_column(Integer, default=15)
    letter_style: Mapped[str] = mapped_column(String(40), default="professional")
    opt_in: Mapped[list] = mapped_column(JSON, default=list)   # platforms you let the app automate despite their terms, e.g. ["linkedin"]


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("user_id", "provider"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(40), default="groq")
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    last4: Mapped[str] = mapped_column(String(8))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LlmUsage(Base):
    __tablename__ = "llm_usage"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    model: Mapped[str] = mapped_column(String(100))
    purpose: Mapped[str] = mapped_column(String(60))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str] = mapped_column(Text, default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("user_id", "platform", "source_id"), Index("ix_jobs_user_created", "user_id", "created_at"))
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    platform: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[str] = mapped_column(String(300))
    url: Mapped[str] = mapped_column(String(2000))
    apply_url: Mapped[str] = mapped_column(String(2000), default="")
    ats: Mapped[str] = mapped_column(String(40), default="")
    title: Mapped[str] = mapped_column(String(300))
    company: Mapped[str] = mapped_column(String(300), default="")
    location: Mapped[str] = mapped_column(String(300), default="")
    work_mode: Mapped[str] = mapped_column(String(20), default="")       # remote | hybrid | onsite | ""
    employment_type: Mapped[str] = mapped_column(String(40), default="")  # full-time | internship | contract | ""
    experience: Mapped[str] = mapped_column(String(100), default="")
    salary_text: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    skills: Mapped[list] = mapped_column(JSON, default=list)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    bookmarked: Mapped[bool] = mapped_column(Boolean, default=False)
    gates: Mapped[dict] = mapped_column(JSON, default=dict)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    last_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class JobScore(Base):
    __tablename__ = "job_scores"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    profile_version: Mapped[int] = mapped_column(Integer)
    score: Mapped[int] = mapped_column(Integer)
    confidence: Mapped[str] = mapped_column(String(10))
    recommendation: Mapped[str] = mapped_column(String(40))
    result: Mapped[dict] = mapped_column(JSON)       # full validated ranking output
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SavedSearch(Base):
    __tablename__ = "saved_searches"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    platform: Mapped[str] = mapped_column(String(40))
    params: Mapped[dict] = mapped_column(JSON)
    saved: Mapped[bool] = mapped_column(Boolean, default=False)   # False = history entry
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_count: Mapped[int] = mapped_column(Integer, default=0)


class CoverLetter(Base):
    __tablename__ = "cover_letters"
    __table_args__ = (UniqueConstraint("user_id", "job_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    style: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CoverLetterVersion(Base):
    __tablename__ = "cover_letter_versions"
    __table_args__ = (UniqueConstraint("cover_letter_id", "number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    cover_letter_id: Mapped[int] = mapped_column(ForeignKey("cover_letters.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    paragraphs: Mapped[list] = mapped_column(JSON)     # list of strings
    source: Mapped[str] = mapped_column(String(20))    # generated | edited | regenerated
    issues: Mapped[list] = mapped_column(JSON, default=list)   # evidence-check findings at save time
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("user_id", "job_id"), Index("ix_app_user_state", "user_id", "state"))
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    state: Mapped[str] = mapped_column(String(30))
    platform: Mapped[str] = mapped_column(String(40))
    resume_id: Mapped[int | None] = mapped_column(ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True)
    cover_letter_version_id: Mapped[int | None] = mapped_column(ForeignKey("cover_letter_versions.id", ondelete="SET NULL"), nullable=True)
    score_at_apply: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lock_owner: Mapped[str] = mapped_column(String(64), default="")
    lock_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    blocker: Mapped[str] = mapped_column(Text, default="")      # what the user must do (Awaiting User / Blocked)
    pending: Mapped[list] = mapped_column(JSON, default=list)   # questions waiting for your answer: {question, options, sensitive}
    error: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)  # {kind, detail, url, screenshot}
    reference: Mapped[str] = mapped_column(String(200), default="")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class ApplicationAnswer(Base):
    __tablename__ = "application_answers"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20))      # profile | rule | user | ai
    field_type: Mapped[str] = mapped_column(String(30), default="text")


class ApplicationSnapshot(Base):
    __tablename__ = "application_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), unique=True)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)      # encrypted JSON of exactly what was submitted
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Execution(Base):
    __tablename__ = "executions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="queued")   # queued | running | completed | failed | waiting | paused | cancelled
    correlation_id: Mapped[str] = mapped_column(String(36), unique=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExecutionEvent(Base):
    __tablename__ = "execution_events"
    __table_args__ = (Index("ix_events_user_ts", "user_id", "id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    execution_id: Mapped[int | None] = mapped_column(ForeignKey("executions.id", ondelete="CASCADE"), nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    level: Mapped[str] = mapped_column(String(10), default="info")      # info | success | warn | error | wait
    operation: Mapped[str] = mapped_column(String(80))
    skill: Mapped[str] = mapped_column(String(80), default="")
    platform: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(20), default="running")
    message: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class PlatformSession(Base):
    __tablename__ = "platform_sessions"
    __table_args__ = (UniqueConstraint("user_id", "platform"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    platform: Mapped[str] = mapped_column(String(40))
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------- engine and migrations ----------

MIGRATIONS: list = [   # (version, callable(conn)) for databases made by an older version; append, never edit
    (2, lambda conn: conn.execute(text("ALTER TABLE user_settings ADD COLUMN opt_in JSON DEFAULT '[]'"))),
]


def _engine(url: str):
    eng = create_engine(url, future=True, connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON"); cur.execute("PRAGMA journal_mode=WAL"); cur.close()
    return eng


engine = None
SessionLocal: sessionmaker | None = None


def init(db_path: Path | None = None) -> None:
    """Create or migrate the database. Backs up the file before applying migrations."""
    global engine, SessionLocal
    db_path = db_path or config.db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    existed = db_path.exists()
    engine = _engine(f"sqlite:///{db_path}")
    SessionLocal = sessionmaker(engine, expire_on_commit=False)
    with engine.begin() as conn:
        version = conn.execute(text("PRAGMA user_version")).scalar() or 0
        pending = [(v, fn) for v, fn in MIGRATIONS if v > version]
    if existed and pending:
        backups = db_path.parent / "backups"; backups.mkdir(exist_ok=True)
        shutil.copy2(db_path, backups / f"{db_path.stem}-v{version}-{int(time.time())}.db")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for v, fn in pending if existed else []:   # a new database is created at the latest shape already
            fn(conn); conn.execute(text(f"PRAGMA user_version={v}"))
        if not existed: conn.execute(text(f"PRAGMA user_version={max([v for v, _ in MIGRATIONS], default=1)}"))


def session() -> Session:
    if SessionLocal is None: init()
    return SessionLocal()
