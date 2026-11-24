"""The single background worker: runs executions stored in the database, one at a time, oldest first.
On start it recovers from a crash: interrupted runs are marked failed, and a submission cut off mid-way
becomes 'Submission Unconfirmed' (it is never resent automatically)."""
import threading
import time
import traceback
from sqlalchemy import select

from .. import db, events
from . import states as S

_thread: threading.Thread | None = None
_wake = threading.Event()
_cancel: set[int] = set()
_browser = None


def enqueue(s, user_id: int, kind: str, params: dict) -> db.Execution:
    ex = events.start(s, user_id, kind, params); s.flush(); _wake.set(); return ex


def cancel(s, user_id: int, execution_id: int) -> None:
    ex = s.get(db.Execution, execution_id)
    if not ex or ex.user_id != user_id: raise LookupError("Execution not found")
    if ex.status == "queued": ex.status = "cancelled"
    elif ex.status == "running": _cancel.add(ex.id)


def alive() -> bool:
    return bool(_thread and _thread.is_alive())


def waiting_for(user_id: int) -> int:
    s = db.session()
    try:
        return len(s.scalars(select(db.Application.id).where(db.Application.user_id == user_id, db.Application.state.in_([S.AWAITING, S.UNCONFIRMED]))).all())
    finally:
        s.close()


def recover(s) -> None:
    for ex in s.scalars(select(db.Execution).where(db.Execution.status == "running")):
        ex.status, ex.finished_at, ex.summary = "failed", db.now(), "Interrupted by an app restart"
        events.emit(s, ex.user_id, f"{ex.kind} was interrupted by a restart", execution_id=ex.id, level="warn", status="failed", operation="recovery")
    for a in s.scalars(select(db.Application).where(db.Application.state.in_([S.SUBMITTING, S.PREFLIGHT, S.PREPARING, S.FILLING, S.READY]))):
        if a.state == S.SUBMITTING:
            S.move(a, S.UNCONFIRMED, blocker="The app stopped while submitting. Check the site before doing anything else.")
        else:
            S.move(a, S.FAILED, error="Interrupted by an app restart; queue it again")
        a.lock_owner, a.lock_expires = "", None
    s.commit()


def browser_for(user_id: int):
    global _browser
    from .browser import Browser
    if _browser and _browser.user_id != user_id: _browser.stop(); _browser = None
    if not _browser: _browser = Browser(user_id)
    return _browser


def _process(s, ex: db.Execution) -> None:
    from . import tasks
    ex.status, ex.started_at = "running", db.now(); s.commit()
    try:
        summary = tasks.HANDLERS[ex.kind](s, ex, lambda: ex.id in _cancel)
        ex.status = "cancelled" if ex.id in _cancel else "completed"; ex.summary = summary or ""
    except Exception as e:
        s.rollback(); ex = s.get(db.Execution, ex.id)
        ex.status, ex.summary = "failed", f"{type(e).__name__}: {e}"[:1000]
        events.emit(s, ex.user_id, f"{ex.kind} failed: {e}", execution_id=ex.id, level="error", status="failed", operation=ex.kind)
        traceback.print_exc()
    ex.finished_at = db.now(); _cancel.discard(ex.id); s.commit()


def _drain(deadline: float = float("inf")) -> None:
    while time.monotonic() < deadline:
        s = db.session()
        try:
            ex = s.scalar(select(db.Execution).where(db.Execution.status == "queued").order_by(db.Execution.id))
            if not ex: return
            _process(s, ex)
        finally:
            s.close()


def _loop():
    while True:
        _wake.wait(timeout=2); _wake.clear(); _drain()


def start() -> None:
    global _thread
    if alive(): return
    s = db.session()
    try: recover(s)
    finally: s.close()
    _thread = threading.Thread(target=_loop, name="browser-worker", daemon=True); _thread.start(); _wake.set()


def run_pending_now(timeout: float = 120) -> None:
    """Tests: process the queue synchronously in this thread."""
    _drain(time.monotonic() + timeout)
