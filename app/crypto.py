"""Encryption at rest (Fernet) for API keys, saved site cookies and application snapshots."""
import json
import os
from cryptography.fernet import Fernet, InvalidToken
from . import config


def _key() -> bytes:
    env = os.environ.get("JOBAPPLY_MASTER_KEY")
    if env: return env.encode()
    f = config.instance() / "master.key"
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(Fernet.generate_key()); f.chmod(0o600)
    return f.read_bytes().strip()


def encrypt(data: bytes) -> bytes:
    return Fernet(_key()).encrypt(data)


def decrypt(token: bytes) -> bytes:
    try:
        return Fernet(_key()).decrypt(token)
    except InvalidToken as e:
        raise RuntimeError("Could not decrypt stored data: instance/master.key changed or is missing") from e


def encrypt_json(obj) -> bytes:
    return encrypt(json.dumps(obj, ensure_ascii=False).encode())


def decrypt_json(token: bytes):
    return json.loads(decrypt(token))
