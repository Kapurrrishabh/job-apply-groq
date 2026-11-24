"""Per-user Groq API keys, encrypted at rest. The plaintext key only exists in memory while a request runs."""
import re
from sqlalchemy import select
from .. import crypto, db

KEY_SHAPE = re.compile(r"^[A-Za-z0-9_\-]{20,200}$")


def store(s, user_id: int, key: str, provider: str = "groq") -> None:
    key = key.strip()
    if not KEY_SHAPE.match(key): raise ValueError("That does not look like an API key")
    row = s.scalar(select(db.ApiKey).where(db.ApiKey.user_id == user_id, db.ApiKey.provider == provider))
    if not row: row = db.ApiKey(user_id=user_id, provider=provider, ciphertext=b"", last4=""); s.add(row)
    row.ciphertext, row.last4, row.updated_at = crypto.encrypt(key.encode()), key[-4:], db.now()
    s.flush()


def get(s, user_id: int, provider: str = "groq") -> str | None:
    row = s.scalar(select(db.ApiKey).where(db.ApiKey.user_id == user_id, db.ApiKey.provider == provider))
    return crypto.decrypt(row.ciphertext).decode() if row else None


def hint(s, user_id: int, provider: str = "groq") -> str:
    row = s.scalar(select(db.ApiKey).where(db.ApiKey.user_id == user_id, db.ApiKey.provider == provider))
    return f"saved, ends in …{row.last4}" if row else "not set"


def delete(s, user_id: int, provider: str = "groq") -> None:
    row = s.scalar(select(db.ApiKey).where(db.ApiKey.user_id == user_id, db.ApiKey.provider == provider))
    if row: s.delete(row)
