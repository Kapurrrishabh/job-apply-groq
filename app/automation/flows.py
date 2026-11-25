"""Learning a site's application flow: which buttons move a form to its next step. What works is remembered per site
and reused; Groq is asked only when a site uses wording the app hasn't seen, and it can never pick a button that
submits, cancels or withdraws."""
import re
from pydantic import BaseModel
from sqlalchemy import select

from .. import db
from ..ai import service
from ..ai.provider import LLMError
from .browser import SUBMIT

ADVANCE = re.compile(r"^\s*(next|continue|save (and|&) continue|proceed|next step|review|review (your )?application|continue to next step)\s*$", re.I)
NEVER = re.compile(r"(?i)cancel|withdraw|delete|remove|\bback\b|previous|sign out|log ?out|close|discard")
SYSTEM = ("You help move through a job application form. From the buttons shown, pick the one that goes to the next step of "
          "the application. Never pick one that submits, sends, cancels, deletes or leaves the application. Answer null if none fits.")


class Pick(BaseModel):
    label: str | None


class Navigator:
    def __init__(self, s, user_id: int):
        self.s, self.user_id = s, user_id

    def _row(self, host: str) -> db.SiteFlow | None:
        return self.s.scalar(select(db.SiteFlow).where(db.SiteFlow.user_id == self.user_id, db.SiteFlow.host == host))

    def choose(self, host: str, labels: list[str], heading: str) -> tuple[str | None, str]:
        """(button label, how it was chosen: learned | known | ai), or (None, reason)."""
        options = [l for l in dict.fromkeys(x.strip() for x in labels) if l and not SUBMIT.search(l) and not NEVER.search(l)]
        if not options: return None, "no button that moves the form on"
        row = self._row(host)
        for label in (row.advance if row else []):
            if label in options: return label, "learned"
        for label in options:
            if ADVANCE.match(label): return label, "known"
        try:
            pick = service.structured(self.s, self.user_id, "form navigation", SYSTEM,
                                      f"PAGE HEADING: {service.untrusted(heading, 300)}\nBUTTONS: {options}", Pick)
        except LLMError as e:
            return None, f"Groq unavailable ({e.kind})"
        return (pick.label, "ai") if pick.label in options else (None, "Groq found no button that moves the form on")

    def remember(self, host: str, label: str) -> None:
        row = self._row(host)
        if not row: row = db.SiteFlow(user_id=self.user_id, host=host, advance=[]); self.s.add(row)
        if label not in row.advance: row.advance = [label] + row.advance[:9]
        self.s.flush()
