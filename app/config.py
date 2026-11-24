"""Paths and process settings. All user data lives under instance/ (git-ignored); nothing personal is in the repo."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = "openai/gpt-oss-120b"
# gpt-oss spends part of this budget on hidden reasoning: a cover letter used up to ~2,000 tokens (measured 2026-11-24).
DEFAULT_MAX_TOKENS = 6000
SESSION_DAYS = 14                 # web login lifetime
SITE_LOGIN_HOURS = 48             # site cookies that are session-only are kept at least this long
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def instance() -> Path:
    return Path(os.environ.get("JOBAPPLY_INSTANCE", ROOT / "instance"))


def db_path() -> Path:
    return instance() / "jobapply.db"


def user_dir(user_id: int, *parts: str) -> Path:
    p = instance().joinpath("users", str(int(user_id)), *parts)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _env_file() -> dict:
    out, f = {}, ROOT / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1); out[k.strip()] = v.strip()
    return out


def setting(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key) or _env_file().get(key) or default


def host() -> str:
    return setting("APP_HOST", "127.0.0.1")


def port() -> int:
    return int(setting("APP_PORT", "8000"))
