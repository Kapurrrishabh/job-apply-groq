"""Resume files: validated upload, text extraction (PDF, DOCX), versions, default selection, deletion."""
import hashlib
import zipfile
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from sqlalchemy import select, update

from . import config, db

TYPES = {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


class UploadError(ValueError):
    pass


def validate(filename: str, data: bytes) -> str:
    """Return the file extension, or raise with a message the user can act on."""
    ext = Path(filename or "").suffix.lower()
    if ext not in TYPES: raise UploadError("Upload a PDF or Word (.docx) file")
    if not data: raise UploadError("The file is empty")
    if len(data) > config.MAX_UPLOAD_BYTES: raise UploadError(f"The file is larger than {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    if ext == ".pdf" and not data.startswith(b"%PDF"): raise UploadError("That file is not a real PDF")
    if ext == ".docx":
        try:
            with zipfile.ZipFile(BytesIO(data)) as z:
                if "word/document.xml" not in z.namelist(): raise UploadError("That file is not a Word document")
                if sum(i.file_size for i in z.infolist()) > 50 * config.MAX_UPLOAD_BYTES: raise UploadError("That document expands too much to be safe")
        except zipfile.BadZipFile:
            raise UploadError("That file is not a Word document") from None
    return ext


def extract_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)[:100_000]
    import docx
    d = docx.Document(str(path))
    parts = [p.text for p in d.paragraphs] + [c.text for t in d.tables for r in t.rows for c in r.cells]
    return "\n".join(x for x in parts if x.strip())[:100_000]


def add(s, user_id: int, filename: str, data: bytes, label: str = "", kind: str = "uploaded", job_id: int | None = None,
        approved: bool = True) -> db.Resume:
    ext = validate(filename, data)
    sha = hashlib.sha256(data).hexdigest()
    have = s.scalar(select(db.Resume).where(db.Resume.user_id == user_id, db.Resume.sha256 == sha, db.Resume.kind == kind))
    if have: return have
    dest = config.user_dir(user_id, "resumes") / f"{sha[:16]}{ext}"
    dest.write_bytes(data)
    first = not s.scalar(select(db.Resume.id).where(db.Resume.user_id == user_id, db.Resume.kind == "uploaded"))
    r = db.Resume(user_id=user_id, label=label or Path(filename).stem, filename=dest.name, mime=TYPES[ext], size=len(data), sha256=sha,
                  text=extract_text(dest), kind=kind, job_id=job_id, approved=approved, is_default=first and kind == "uploaded")
    s.add(r); s.flush(); return r


def tailored_for(s, user_id: int, job_id: int) -> db.Resume | None:
    return s.scalar(select(db.Resume).where(db.Resume.user_id == user_id, db.Resume.job_id == job_id, db.Resume.kind == "tailored")
                    .order_by(db.Resume.id.desc()))


def get(s, user_id: int, resume_id: int) -> db.Resume:
    r = s.scalar(select(db.Resume).where(db.Resume.id == resume_id, db.Resume.user_id == user_id))
    if not r: raise LookupError("Resume not found")
    return r


def path(r: db.Resume) -> Path:
    return config.user_dir(r.user_id, "resumes") / Path(r.filename).name


def listing(s, user_id: int) -> list[db.Resume]:
    return list(s.scalars(select(db.Resume).where(db.Resume.user_id == user_id).order_by(db.Resume.kind, db.Resume.created_at.desc())))


def default(s, user_id: int) -> db.Resume | None:
    return s.scalar(select(db.Resume).where(db.Resume.user_id == user_id, db.Resume.is_default.is_(True)))


def set_default(s, user_id: int, resume_id: int) -> None:
    r = get(s, user_id, resume_id)
    if not r.approved: raise UploadError("Approve this resume before making it the default")
    s.execute(update(db.Resume).where(db.Resume.user_id == user_id).values(is_default=False))
    r.is_default = True


def delete(s, user_id: int, resume_id: int) -> None:
    r = get(s, user_id, resume_id)
    path(r).unlink(missing_ok=True); s.delete(r)
