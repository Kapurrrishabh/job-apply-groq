"""Local accounts: argon2id password hashes, server-side sessions, CSRF tokens."""
import hashlib
import re
import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import delete, select

from . import config, db

COOKIE = "ja_session"
_hasher = PasswordHasher()
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(ValueError):
    pass


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def register(s, email: str, name: str, password: str) -> db.User:
    email = email.strip().lower()
    if not EMAIL.match(email): raise AuthError("Enter a valid email address")
    if len(password) < 10: raise AuthError("Use a password of at least 10 characters")
    if s.scalar(select(db.User).where(db.User.email == email)): raise AuthError("An account with that email already exists")
    u = db.User(email=email, name=name.strip() or email.split("@")[0], password_hash=_hasher.hash(password))
    s.add(u); s.flush()
    s.add(db.UserSettings(user_id=u.id))
    return u


def authenticate(s, email: str, password: str) -> db.User:
    u = s.scalar(select(db.User).where(db.User.email == email.strip().lower()))
    try:
        if not u: raise VerifyMismatchError
        _hasher.verify(u.password_hash, password)
    except VerifyMismatchError:
        raise AuthError("Email or password is wrong") from None
    if _hasher.check_needs_rehash(u.password_hash): u.password_hash = _hasher.hash(password)
    return u


def start_session(s, user: db.User) -> str:
    token = secrets.token_urlsafe(32)
    s.add(db.AuthSession(user_id=user.id, token_hash=_h(token), csrf=secrets.token_urlsafe(24),
                         expires_at=db.now() + timedelta(days=config.SESSION_DAYS)))
    return token


def session_for(s, token: str | None) -> db.AuthSession | None:
    if not token: return None
    a = s.scalar(select(db.AuthSession).where(db.AuthSession.token_hash == _h(token)))
    if not a: return None
    exp = a.expires_at if a.expires_at.tzinfo else a.expires_at.replace(tzinfo=db.now().tzinfo)
    if exp < db.now():
        s.delete(a); return None
    return a


def end_session(s, token: str | None) -> None:
    if token: s.execute(delete(db.AuthSession).where(db.AuthSession.token_hash == _h(token)))


def csrf_ok(a: db.AuthSession, submitted: str | None) -> bool:
    return bool(submitted) and secrets.compare_digest(a.csrf, submitted)
