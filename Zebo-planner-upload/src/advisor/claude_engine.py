"""OPTIONAL Claude API engine (off by default). Same allowlisted tools, same guardrails, same grounding check.

Enable (later, when ready):
    pip install anthropic
    set ANTHROPIC_API_KEY=...            (Windows)   /  export ANTHROPIC_API_KEY=...
    set NORBAE_ADVISOR_ENGINE=claude
    set NORBAE_CLAUDE_MODEL=claude-sonnet-5-5   (optional; any current Claude model id)

Safety properties enforced in code (not only in the prompt):
- the model can only call tools in PlanningToolkit.SPECS (read-only, scope-checked, argument-validated);
- at most MAX_TOOL_CALLS_PER_TURN tool calls per question;
- employment/individual-ranking and system-write requests are answered by the rule guardrails before any model call;
- the model cannot change the plan: `evaluate_move` results become proposals a human must apply;
- any $ or % in the model's answer that is not traceable to tool output -> the rule-based answer is shown instead.
This adapter is tested only with a fake client in the automated tests.
"""
from __future__ import annotations

import json
import os
from typing import Any

from advisor import grounding
from advisor.rules_engine import _BLOCK, _RANK_PEOPLE, _SENSITIVE, RuleAdvisor
from advisor.tools import MAX_TOOL_CALLS_PER_TURN, PlanningToolkit

SYSTEM = """You are the Norbae AI Planning Advisor for an FY27 annual sales plan (synthetic demo data).
The user is {persona}. You may only see and discuss data inside their access scope.
Use the provided tools for every number. Never calculate new figures yourself; quote tool output.
Revenue "FY26 YTD" covers Jan–Nov (December has not closed). Say so when comparing years.
You cannot change the plan. When you evaluate a move, present the verdict and reasons; the user applies it.
Never rank individual sellers for performance or employment decisions.
If the data does not answer the question, say what is missing. Be concise; use short bullets.
Current focus: {focus}."""


class ClaudeAdvisor:
    def __init__(self, toolkit_factory, client=None, model: str | None = None, max_tokens: int = 1200):
        self._factory = toolkit_factory
        self._fallback = RuleAdvisor(toolkit_factory)
        self.model = model or os.environ.get("NORBAE_CLAUDE_MODEL", "claude-sonnet-5-5")
        self.name = f"Claude API ({self.model})"
        self.max_tokens = max_tokens
        if client is None:
            import anthropic  # imported lazily: optional dependency
            client = anthropic.Anthropic()   # reads ANTHROPIC_API_KEY from the environment
        self.client = client

    def briefing(self, ctx: dict[str, Any]) -> dict[str, Any]:
        return self.respond("Brief me on my business.", ctx)

    def respond(self, message: str, ctx: dict[str, Any]) -> dict[str, Any]:
        msg = (message or "")[:1500]
        if _SENSITIVE.search(msg) or _RANK_PEOPLE.search(msg) or _BLOCK.search(msg):
            r = self._fallback.respond(msg, ctx)
            r["engine_note"] = "Handled by a code-level guardrail before any model call."
            return r
        tk: PlanningToolkit = self._factory()
        focus = ctx.get("focus")
        messages: list[dict[str, Any]] = [{"role": "user", "content": msg}]
        results: list[Any] = []
        proposals: list[dict[str, Any]] = []
        new_focus = focus
        try:
            for _ in range(MAX_TOOL_CALLS_PER_TURN + 1):
                resp = self.client.messages.create(
                    model=self.model, max_tokens=self.max_tokens,
                    system=SYSTEM.format(persona=tk.persona.label, focus=f"{focus[0]} {focus[1]}" if focus else "none"),
                    tools=tk.json_schemas(), messages=messages)
                if resp.stop_reason != "tool_use":
                    text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text").strip()
                    break
                messages.append({"role": "assistant", "content": resp.content})
                tool_results = []
                for b in resp.content:
                    if getattr(b, "type", "") != "tool_use":
                        continue
                    out = tk.call(b.name, **(b.input or {}))
                    results.append(out)
                    if b.name == "evaluate_move" and "error" not in out:
                        proposals.append(out)
                    if b.name == "drill" and "error" not in out:
                        new_focus = (out["level"], out["id"])
                    tool_results.append({"type": "tool_result", "tool_use_id": b.id,
                                         "content": json.dumps(out, default=str)[:12000]})
                messages.append({"role": "user", "content": tool_results})
            else:
                raise RuntimeError("turn limit reached")
        except Exception as exc:  # network, auth, quota, malformed output
            r = self._fallback.respond(msg, ctx)
            r["engine_note"] = f"Claude API unavailable ({type(exc).__name__}); showing the rule-based answer."
            return r
        g = grounding.check(text, results)
        if not g["all_traceable"] or not text:
            r = self._fallback.respond(msg, ctx)
            r["engine_note"] = ("Model answer had figures not found in tool output "
                                f"({', '.join(g['untraceable'][:3])}); showing the rule-based answer.")
            return r
        ctx["focus"] = new_focus
        return {"text": text, "proposals": proposals, "suggestions": [], "focus": new_focus,
                "tool_calls": tk.calls, "engine": self.name, "grounding": g}


def get_advisor(toolkit_factory):
    """Factory: rule engine by default; Claude only when explicitly enabled and configured."""
    if os.environ.get("NORBAE_ADVISOR_ENGINE", "rules").lower() == "claude" and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return ClaudeAdvisor(toolkit_factory)
        except Exception:   # anthropic not installed, etc.
            pass
    return RuleAdvisor(toolkit_factory)
