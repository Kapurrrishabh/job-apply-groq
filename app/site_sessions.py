"""Saved job-site logins per user: cookies encrypted at rest, session-only cookies kept for at least
config.SITE_LOGIN_HOURS so a restart does not sign you out. Never stored in application history."""
import time
from datetime import datetime, timezone
from sqlalchemy import select
from . import config, crypto, db


def _file(user_id: int, platform: str):
    return config.user_dir(user_id, "sessions") / f"{platform}.bin"


def extend(cookies: list[dict], now: float, hours: int) -> list[dict]:
    until = now + hours * 3600
    return [{**c, "expires": until} if c.get("expires", -1) <= 0 else c for c in cookies]


def save(s, user_id: int, platform: str, cookies: list[dict], domain: str | None = None) -> db.PlatformSession:
    now = time.time()
    mine = [c for c in cookies if not domain or c.get("domain", "").lstrip(".").endswith(domain)]
    mine = extend(mine, now, config.SITE_LOGIN_HOURS)
    f = _file(user_id, platform); f.write_bytes(crypto.encrypt_json(mine)); f.chmod(0o600)
    live = [c["expires"] for c in mine if c.get("expires", 0) > now]
    row = s.scalar(select(db.PlatformSession).where(db.PlatformSession.user_id == user_id, db.PlatformSession.platform == platform))
    if not row: row = db.PlatformSession(user_id=user_id, platform=platform); s.add(row)
    row.saved_at = db.now()
    row.expires_at = datetime.fromtimestamp(max(live), timezone.utc) if live else None
    s.flush(); return row


def load_all(user_id: int) -> list[dict]:
    now, out = time.time(), []
    for f in config.user_dir(user_id, "sessions").glob("*.bin"):
        out += [c for c in crypto.decrypt_json(f.read_bytes()) if c.get("expires", 0) > now]
    return out


def status(s, user_id: int) -> dict[str, db.PlatformSession]:
    rows = s.scalars(select(db.PlatformSession).where(db.PlatformSession.user_id == user_id))
    out = {}
    for r in rows:
        exp = r.expires_at.replace(tzinfo=timezone.utc) if r.expires_at and not r.expires_at.tzinfo else r.expires_at
        if exp and exp > db.now(): out[r.platform] = r
    return out


def forget(s, user_id: int, platform: str) -> None:
    _file(user_id, platform).unlink(missing_ok=True)
    row = s.scalar(select(db.PlatformSession).where(db.PlatformSession.user_id == user_id, db.PlatformSession.platform == platform))
    if row: s.delete(row)
