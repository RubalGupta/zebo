"""OPTIONAL model integration — disabled by default.

The application is complete without this module. When enabled, a model may only:
  1. choose tools from the fixed allowlist (validated in code by ``ToolSession``), and
  2. explain the results.
It cannot write to business systems, cannot exceed the tool-call limit, and its
answer is rejected (falling back to the no-LLM baseline answer) if it contains a
dollar or percentage figure that is not traceable to tool output.

Supported provider: ``ollama`` — a free, locally-run model server (https://ollama.com).
No API key is used or stored. Only localhost URLs are accepted so that planning
data never leaves the machine.

Configuration (environment variables only; see ``.env.example``):
    NORBAE_LLM_PROVIDER = none | ollama        (default: none)
    NORBAE_OLLAMA_MODEL = e.g. llama3.1:8b
    NORBAE_OLLAMA_URL   = http://localhost:11434
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

from agent_blueprint import ToolSession, tool_catalog
from config import DEFAULT_THRESHOLDS, MAX_TOOL_CALLS_PER_QUESTION, Thresholds
from intent_router import answer_question, classify, grounding_check
from review_store import ReviewStore

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class ModelClient(Protocol):
    name: str

    def complete(self, messages: list[dict[str, str]]) -> str: ...


@dataclass
class LLMConfig:
    provider: str = "none"
    model: str = ""
    url: str = "http://localhost:11434"

    @property
    def enabled(self) -> bool:
        return self.provider == "ollama" and bool(self.model)

    def public(self) -> dict[str, Any]:
        return {"provider": self.provider, "model": self.model if self.enabled else "", "enabled": self.enabled}


def get_llm_config(env: dict[str, str] | None = None) -> LLMConfig:
    env = env if env is not None else os.environ
    provider = (env.get("NORBAE_LLM_PROVIDER") or "none").strip().lower()
    if provider not in {"none", "ollama"}:
        provider = "none"
    return LLMConfig(provider=provider, model=(env.get("NORBAE_OLLAMA_MODEL") or "").strip(),
                     url=(env.get("NORBAE_OLLAMA_URL") or "http://localhost:11434").strip())


class OllamaClient:
    """Minimal stdlib client for a local Ollama server. Not exercised by the automated tests."""

    def __init__(self, model: str, url: str = "http://localhost:11434", timeout: float = 60.0):
        host = urlparse(url).hostname
        if host not in LOCAL_HOSTS:
            raise ValueError("Only a local Ollama URL (localhost) is allowed")
        self.name = f"ollama:{model}"
        self.model, self.url, self.timeout = model, url.rstrip("/"), timeout

    def complete(self, messages: list[dict[str, str]]) -> str:
        body = json.dumps({"model": self.model, "messages": messages, "stream": False,
                           "format": "json", "options": {"temperature": 0}}).encode()
        req = urllib.request.Request(self.url + "/api/chat", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - localhost only
            return json.loads(resp.read().decode())["message"]["content"]


SYSTEM_PROMPT = """You are the Norbae AI Annual Sales Planning Advisor (synthetic demo data).
Rules:
- You may ONLY use these read-only tools: {tools}
- Reply with exactly one JSON object per turn, either
  {{"tool": "<name>", "args": {{...}}}}  or  {{"final": "<answer>"}}
- At most {max_calls} tool calls per question.
- Every number in your final answer must come from a tool result. Do not calculate new numbers.
- If the data does not answer the question, say what is missing. Never invent business context.
- You cannot change quotas, territories, CRM, or any system. You can only suggest a draft for human review.
- Never rank individual reps or support employment decisions.
"""


def run_llm_agent(question: str, client: ModelClient, *, data_dir=None, thresholds: Thresholds = DEFAULT_THRESHOLDS,
                  store: ReviewStore | None = None, max_calls: int = MAX_TOOL_CALLS_PER_QUESTION) -> dict[str, Any]:
    """Bounded tool-calling loop. Falls back to the no-LLM baseline on any failure or ungrounded answer."""
    intent = classify(question)
    if intent in {"sensitive_individual_decision", "blocked_action", "empty"}:
        r = answer_question(question, data_dir=data_dir, thresholds=thresholds, store=store)
        r["llm_note"] = "Handled by code-level guardrail before any model call."
        return r

    session = ToolSession(max_calls=max_calls, data_dir=data_dir, thresholds=thresholds, store=store)
    names = ", ".join(t["name"] for t in tool_catalog() if t["kind"] == "read_only")
    messages = [{"role": "system", "content": SYSTEM_PROMPT.format(tools=names, max_calls=max_calls)},
                {"role": "user", "content": question[:2000]}]
    results: list[dict[str, Any]] = []
    final: str | None = None
    failure = ""
    for _turn in range(max_calls + 1):  # hard upper bound on model turns
        try:
            raw = client.complete(messages)
            msg = json.loads(raw)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            failure = f"Model unavailable ({type(exc).__name__})."
            break
        except (json.JSONDecodeError, KeyError, TypeError):
            failure = "Model returned an invalid response."
            break
        if not isinstance(msg, dict):
            failure = "Model returned an invalid response."
            break
        if "final" in msg:
            final = str(msg["final"])[:4000]
            break
        tool = msg.get("tool")
        args = msg.get("args") or {}
        if tool == "create_change_request" or not isinstance(args, dict):
            failure = "Model requested a disallowed action."
            break
        result = session.call(str(tool), **{str(k)[:40]: v for k, v in list(args.items())[:5]})
        results.append(result)
        if result.get("error") == "Tool-call limit reached":
            failure = "Model exceeded the tool-call limit."
            break
        messages.append({"role": "assistant", "content": json.dumps(msg)[:2000]})
        messages.append({"role": "user", "content": "TOOL RESULT: " + json.dumps(result, default=str)[:6000]})
    else:
        failure = "Model did not produce a final answer within the turn limit."

    if final is not None:
        grounding = grounding_check(final, results, thresholds)
        if grounding["all_traceable"] and (results or grounding["figures_checked"] == 0):
            if store is not None:
                store.append_audit("advisor_question", mode="optional_llm", model=client.name,
                                   tool_count=len(session.calls), grounded=True)
            return {"mode": "optional_llm", "mode_label": f"Optional local model ({client.name}) — experimental",
                    "intent": "llm", "answer": final, "tool_calls": session.calls, "tool_results": results,
                    "grounding": grounding, "note": "Model-written explanation; all figures verified against tool output."}
        failure = "Model answer contained figures not found in tool output: " + ", ".join(grounding["untraceable"][:5])

    r = answer_question(question, data_dir=data_dir, thresholds=thresholds, store=store)
    r["llm_note"] = f"{failure} Showing the no-LLM baseline answer instead."
    return r
