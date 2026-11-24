"""Saved answers to application questions (per user), with sensitive questions kept apart."""
import re
from sqlalchemy import delete, func, select
from . import db

# Questions whose answers carry legal weight or personal data: answered only from rules the user saved, never guessed.
SENSITIVE = re.compile(r"(?i)salary|ctc|compensation|stipend|pay expectation|authori[sz]|legally|right to work|sponsor|visa|"
                       r"gender|sex\b|race|ethnic|veteran|disabilit|pronoun|religion|caste|marital|age\b|date of birth|"
                       r"criminal|convict|background check|non.?compete|declare|attest|certify|i agree|terms|consent|signature|"
                       r"relative|government official|securities")


def is_sensitive(question: str) -> bool:
    return bool(SENSITIVE.search(question or ""))


def rules(s, user_id: int) -> list[db.AnswerRule]:
    return list(s.scalars(select(db.AnswerRule).where(db.AnswerRule.user_id == user_id).order_by(db.AnswerRule.position, db.AnswerRule.id)))


def pick(answer: str, options: list[str]) -> str | None:
    a = answer.strip().lower()
    for o in options:
        if o.strip().lower() == a: return o
    for o in options:
        if o.strip().lower().startswith(a): return o
    return None


def lookup(rule_list: list, question: str, options: list[str] | None = None) -> str | None:
    q = " ".join((question or "").lower().split())
    for r in rule_list:
        pattern, answer = (r.pattern, r.answer) if hasattr(r, "pattern") else (r["pattern"], r["answer"])
        try:
            if re.search(pattern, q): return pick(answer, options) if options else answer
        except re.error:
            continue
    return None


def remember(s, user_id: int, question: str, answer: str, note: str = "saved from an application") -> None:
    """Save an exact-question rule ahead of the general ones."""
    pat = "^" + re.escape(" ".join(question.lower().split())) + "$"
    s.execute(delete(db.AnswerRule).where(db.AnswerRule.user_id == user_id, db.AnswerRule.pattern == pat))
    first = s.scalar(select(func.min(db.AnswerRule.position)).where(db.AnswerRule.user_id == user_id)) or 0
    s.add(db.AnswerRule(user_id=user_id, position=first - 1, pattern=pat, answer=answer, note=note, sensitive=is_sensitive(question)))
    s.flush()
