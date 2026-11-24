"""A real Chrome window per user (persistent profile, sandbox on). Only the worker thread touches it."""
import os
import re
from playwright.sync_api import sync_playwright
from .. import config

SUBMIT = re.compile(r"\b(submit|send application|finish application|complete application)\b", re.I)
# Challenge wording only: "protected by reCAPTCHA" notices and invisible checks are not something you must do.
BARRIER = re.compile(r"(?i)(complete|solve) the captcha|captcha (is )?required|verify (that )?you are (a )?human|are you a robot|one.time password|"
                     r"\botp\b|enter the (verification )?code|security check|two.step verification")
CHALLENGE_FRAMES = ("iframe[src*='recaptcha'][src*='anchor']:not([src*='size=invisible']), iframe[src*='recaptcha'][src*='bframe'], "
                    "iframe[src*='hcaptcha'], iframe[src*='challenges.cloudflare']")

FIELDS_JS = r"""() => {
  const out = [];
  const text = el => (el ? (el.innerText || el.textContent || '').trim() : '');
  const before = el => {   // the question text just above a field that has no <label>
    for (let n = el, i = 0; n && i < 3; n = n.parentElement, i++)
      for (let p = n.previousElementSibling; p; p = p.previousElementSibling) { const t = text(p); if (t && t.length < 300) return t; if (t) break; }
    return '';
  };
  document.querySelectorAll('input, select, textarea, button, [role=combobox], [role=radio], [role=checkbox]').forEach((el, i) => {
    if (el.type === 'hidden' || el.getAttribute('aria-hidden') === 'true') return;   // helpers people never see
    const r = el.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden') && el.type !== 'file') return;
    el.setAttribute('data-ajs', String(i));
    let label = '';
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) label = text(l); }
    if (!label) label = text(el.closest('label'));
    if (!label && el.getAttribute('aria-labelledby')) label = text(document.getElementById(el.getAttribute('aria-labelledby')));
    const fs = el.closest('fieldset'), rg = el.closest('[role=radiogroup], [role=group]');
    let group = fs ? text(fs.querySelector('legend')) : (rg ? (rg.getAttribute('aria-label') || '') : '');
    if (!group && el.type === 'radio') group = before(el.closest('label') || el);
    if (!label && !el.getAttribute('aria-label') && !el.placeholder && el.type !== 'radio') label = before(el);
    out.push({sel: `[data-ajs="${i}"]`, tag: el.tagName.toLowerCase(), type: (el.type || el.getAttribute('role') || '').toLowerCase(),
      name: el.name || '', id: el.id || '', aria: el.getAttribute('aria-label') || '', placeholder: el.placeholder || '',
      label: label.slice(0, 300), group: group.slice(0, 300), value: (el.value || '').slice(0, 300), checked: !!el.checked,
      text: text(el).slice(0, 120), required: !!(el.required || el.getAttribute('aria-required') === 'true'),
      combo: el.getAttribute('role') === 'combobox' || el.getAttribute('aria-autocomplete') === 'list',
      options: el.tagName === 'SELECT' ? [...el.options].map(o => o.text.trim()) : null});
  });
  return out;
}"""


class SubmitRefused(RuntimeError):
    pass


class Browser:
    def __init__(self, user_id: int):
        self.user_id, self._pw, self.ctx, self.page = user_id, None, None, None

    @property
    def headless(self) -> bool:
        return os.environ.get("JOBAPPLY_HEADLESS") == "1"

    def start(self):
        if self.ctx: return
        from .. import site_sessions
        self._pw = sync_playwright().start()
        profile = config.instance() / "users" / str(self.user_id) / "browser-profile"
        profile.mkdir(parents=True, exist_ok=True)
        # Playwright passes --no-sandbox unless chromium_sandbox=True; keep Chrome's sandbox on.
        opts = dict(headless=self.headless, viewport=None if not self.headless else {"width": 1280, "height": 900}, chromium_sandbox=True)
        try:
            self.ctx = self._pw.chromium.launch_persistent_context(str(profile), channel="chrome", **opts)
        except Exception:
            self.ctx = self._pw.chromium.launch_persistent_context(str(profile), **opts)
        saved = site_sessions.load_all(self.user_id)
        if saved: self.ctx.add_cookies(saved)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()

    def stop(self):
        try:
            if self.ctx: self.ctx.close()
            if self._pw: self._pw.stop()
        finally:
            self._pw = self.ctx = self.page = None

    def p(self):
        self.start()
        if self.page.is_closed(): self.page = self.ctx.pages[-1] if self.ctx.pages else self.ctx.new_page()
        return self.page

    def window(self, state: str):
        if self.headless: return
        p = self.p(); cdp = self.ctx.new_cdp_session(p)
        wid = cdp.send("Browser.getWindowForTarget")["windowId"]
        cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": state}})
        if state == "normal": p.bring_to_front()

    def goto(self, url: str, timeout: int = 60000):
        self.p().goto(url, wait_until="domcontentloaded", timeout=timeout)

    def url(self) -> str:
        return self.p().url

    def text(self, limit: int = 200000) -> str:
        return self.p().evaluate("() => document.body ? document.body.innerText : ''")[:limit]

    def barrier(self) -> str | None:
        """A CAPTCHA / OTP / verification step the user must complete themselves, if one is showing."""
        t = self.text(20000)
        m = BARRIER.search(t)
        if m: return m.group(0)
        for frame in self.p().query_selector_all(CHALLENGE_FRAMES):
            box = frame.bounding_box()
            if frame.is_visible() and box and box["width"] > 50 and box["height"] > 50: return "CAPTCHA"
        return None

    def fields(self) -> list[dict]:
        return self.p().evaluate(FIELDS_JS)

    def fill(self, sel: str, value: str):
        self.p().fill(sel, value, timeout=15000)

    def type(self, sel: str, value: str):
        p = self.p(); p.click(sel, timeout=15000); p.keyboard.type(value, delay=15)

    def press(self, key: str):
        self.p().keyboard.press(key)

    def select(self, sel: str, label: str):
        self.p().select_option(sel, label=label, timeout=15000)

    def check(self, sel: str):
        self.p().check(sel, timeout=15000)

    def upload(self, sel: str, path: str):
        self.p().set_input_files(sel, path, timeout=15000)

    def click(self, sel: str, submit: bool = False) -> str:
        p = self.p()
        txt = p.eval_on_selector(sel, "e => (e.innerText || e.value || e.getAttribute('aria-label') || '').trim()")
        if SUBMIT.search(txt) and not submit:
            raise SubmitRefused(f"'{txt}' looks like a final submit button; it is only pressed after you authorize the application")
        p.click(sel, timeout=15000); p.wait_for_timeout(1200)
        return txt

    def screenshot(self, path: str):
        self.p().screenshot(path=path, full_page=True)

    def cookies(self) -> list[dict]:
        self.start(); return self.ctx.cookies()

    def wait(self, ms: int):
        self.p().wait_for_timeout(ms)
