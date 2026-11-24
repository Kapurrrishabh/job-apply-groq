"""Groq chooses tools; the app validates, executes and reports them. Max steps bound every run."""
import json
from .. import events
from ..ai import service
from . import tools

SYSTEM = ("You are the job-search assistant inside a personal application tracker. Use the tools to do what the user asks: "
          "search jobs (freehire only; LinkedIn, Naukri and Internshala jobs must be imported by the user), rank them, explain "
          "results, draft cover letters, and select jobs for the user's review. You cannot submit applications: tell the user "
          "to press Apply on the Queue page. Never invent jobs, scores or results; report only what tools return. Be brief.")
MAX_STEPS = 8


def run(s, user_id: int, prompt: str, execution_id: int, cancelled=lambda: False) -> str:
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": service.untrusted(prompt, 2000)}]
    events.emit(s, user_id, f"Agent: {prompt[:200]}", execution_id=execution_id, operation="agent", skill="orchestrator")
    for step in range(MAX_STEPS):
        if cancelled(): return "stopped"
        reply = service.tool_turn(s, user_id, messages, tools.schemas())
        if not reply.tool_calls:
            events.emit(s, user_id, reply.content[:1500] or "(no answer)", execution_id=execution_id, operation="agent", level="success", status="completed")
            return reply.content[:1000]
        messages.append({"role": "assistant", "content": reply.content or "",
                         "tool_calls": [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}} for c in reply.tool_calls]})
        for c in reply.tool_calls:
            events.emit(s, user_id, f"Tool {c['name']}({json.dumps(c['arguments'])[:200]})", execution_id=execution_id, operation="tool", skill=c["name"])
            result = tools.call(s, user_id, c["name"], c["arguments"], execution_id); s.commit()
            level = "error" if "error" in result else "success"
            events.emit(s, user_id, f"{c['name']} → {json.dumps(result)[:300]}", execution_id=execution_id, operation="tool", skill=c["name"], level=level, status="completed")
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(result)[:6000]})
    events.emit(s, user_id, "Agent stopped after the step limit", execution_id=execution_id, operation="agent", level="warn", status="completed")
    return "step limit reached"
