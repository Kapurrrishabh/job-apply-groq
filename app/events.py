"""Execution events: the real activity behind the console. Messages are redacted before they are stored."""
import re
import time
import uuid
from contextlib import contextmanager
from sqlalchemy import select
from . import db

REDACT = [(re.compile(r"gsk_[A-Za-z0-9]{10,}"), "gsk_…"), (re.compile(r"(?i)(password|passwd|token|cookie|secret)\s*[:=]\s*\S+"), r"\1=…"),
          (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"), "Bearer …")]


def redact(text: str) -> str:
    for pat, rep in REDACT: text = pat.sub(rep, text)
    return text


def start(s, user_id: int, kind: str, params: dict | None = None) -> db.Execution:
    e = db.Execution(user_id=user_id, kind=kind, correlation_id=str(uuid.uuid4()), params=params or {}, status="queued")
    s.add(e); s.flush(); return e


def emit(s, user_id: int, message: str, *, execution_id: int | None = None, level: str = "info", operation: str = "",
         skill: str = "", platform: str = "", status: str = "running", detail: dict | None = None, duration_ms: int | None = None):
    clean = {k: redact(str(v)) if isinstance(v, str) else v for k, v in (detail or {}).items()}
    ev = db.ExecutionEvent(user_id=user_id, execution_id=execution_id, level=level, operation=operation or "event",
                           skill=skill, platform=platform, status=status, message=redact(message)[:2000], detail=clean,
                           duration_ms=duration_ms)
    s.add(ev); s.commit(); return ev


@contextmanager
def step(s, user_id: int, operation: str, message: str, **kw):
    """Emit 'running', then 'completed' with duration, or 'failed' with the error, around a block."""
    t = time.monotonic()
    emit(s, user_id, message, operation=operation, **kw)
    try:
        yield
    except Exception as e:
        emit(s, user_id, f"{message}: failed ({e})", operation=operation, level="error", status="failed",
             duration_ms=int((time.monotonic() - t) * 1000), **{k: v for k, v in kw.items() if k != "level"})
        raise
    emit(s, user_id, f"{message}: done", operation=operation, level="success", status="completed",
         duration_ms=int((time.monotonic() - t) * 1000), **{k: v for k, v in kw.items() if k != "level"})


def since(s, user_id: int, after_id: int = 0, limit: int = 200) -> list[db.ExecutionEvent]:
    q = select(db.ExecutionEvent).where(db.ExecutionEvent.user_id == user_id, db.ExecutionEvent.id > after_id)
    return list(s.scalars(q.order_by(db.ExecutionEvent.id.desc()).limit(limit)))[::-1]
