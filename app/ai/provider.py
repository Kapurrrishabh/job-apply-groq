"""LLM providers. GroqProvider talks to Groq's OpenAI-compatible API; FakeProvider scripts replies for tests."""
import json
import time
from dataclasses import dataclass, field

import httpx

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
STRICT_MODELS = {"openai/gpt-oss-120b", "openai/gpt-oss-20b"}   # json_schema strict mode (Groq docs, checked 2026-11-17)
MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]


class LLMError(RuntimeError):
    def __init__(self, kind: str, message: str):
        super().__init__(message); self.kind = kind     # no_key | auth | rate_limit | timeout | bad_request | server | invalid_output


@dataclass
class Reply:
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)   # [{id, name, arguments: dict}]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0


class GroqProvider:
    def __init__(self, api_key: str, retries: int = 4, timeout: float = 90):
        self.key, self.retries, self.timeout = api_key, retries, timeout

    def chat(self, messages: list[dict], *, model: str, temperature: float, max_tokens: int,
             schema: dict | None = None, tools: list[dict] | None = None) -> Reply:
        body = {"model": model, "messages": messages, "temperature": temperature, "max_completion_tokens": max_tokens}
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]; body["tool_choice"] = "auto"
        elif schema and model in STRICT_MODELS:
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "result", "strict": True, "schema": schema}}
        elif schema:
            body["response_format"] = {"type": "json_object"}
        delay, t0 = 2.0, time.monotonic()
        for attempt in range(self.retries + 1):
            try:
                r = httpx.post(GROQ_URL, json=body, headers={"Authorization": f"Bearer {self.key}"}, timeout=self.timeout)
            except httpx.TimeoutException:
                if attempt == self.retries: raise LLMError("timeout", "Groq did not answer in time") from None
                time.sleep(delay); delay = min(delay * 2, 30); continue
            except httpx.HTTPError as e:
                raise LLMError("server", f"Could not reach Groq ({type(e).__name__})") from None
            if r.status_code in (401, 403): raise LLMError("auth", "Groq rejected the API key; check it on the Settings page")
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == self.retries:
                    raise LLMError("rate_limit" if r.status_code == 429 else "server", f"Groq returned {r.status_code} after {self.retries + 1} tries")
                time.sleep(min(float(r.headers.get("retry-after") or delay), 60)); delay = min(delay * 2, 30); continue
            if r.status_code >= 400 and (r.json().get("error") or {}).get("code") == "json_validate_failed":
                raise LLMError("invalid_output", "Groq could not produce valid JSON for this answer")
            if r.status_code >= 400:
                raise LLMError("bad_request", f"Groq returned {r.status_code}: {r.json().get('error', {}).get('message', '')[:300]}")
            data = r.json(); msg = data["choices"][0]["message"]; usage = data.get("usage") or {}
            calls = [{"id": c["id"], "name": c["function"]["name"], "arguments": _args(c["function"].get("arguments"))}
                     for c in msg.get("tool_calls") or []]
            return Reply(msg.get("content") or "", calls, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
                         int((time.monotonic() - t0) * 1000))
        raise LLMError("server", "unreachable")


def _args(raw) -> dict:
    try:
        return json.loads(raw) if isinstance(raw, str) else (raw or {})
    except json.JSONDecodeError:
        return {"__invalid__": raw}


class FakeProvider:
    """Replies come from `script`, a list of Reply objects or callables(messages) -> Reply, consumed in order."""
    def __init__(self, script: list):
        self.script, self.calls = list(script), []

    def chat(self, messages, **kw) -> Reply:
        self.calls.append({"messages": messages, **kw})
        if not self.script: raise LLMError("server", "FakeProvider has no scripted reply left")
        nxt = self.script.pop(0)
        if isinstance(nxt, Exception): raise nxt
        return nxt(messages) if callable(nxt) else nxt
