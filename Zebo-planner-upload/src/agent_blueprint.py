"""Provider-neutral agent contract for the Norbae AI Annual Sales Planning Advisor.

Defines the tool boundary and approval policy without requiring an LLM provider.
Everything here is enforced in Python code, not in a prompt:

- a FIXED allowlist of read-only tools (``TOOLS``);
- typed/validated arguments per tool; unknown or extra arguments are rejected;
- unknown tools fail closed with ``{"error": "Tool not allowlisted"}``;
- at most ``MAX_TOOL_CALLS_PER_QUESTION`` (3) tool calls per question (``ToolSession``);
- every call is logged (tool name, safe arguments, status, timestamp) to the local
  demo audit log -- never raw user text or secrets;
- ``create_change_request`` returns a DRAFT record only. It never changes quota,
  territory, CRM, or source-of-truth data.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from analysis_tools import (
    DataValidationError,
    check_data_consistency,
    find_planning_exceptions,
    get_territory_detail,
    list_data_gaps,
    load_quota_plan,
    money,
    normalize_territory_id,
    preview_quota_scenario,
    summarize_plan,
)
from config import DEFAULT_THRESHOLDS, MAX_TOOL_CALLS_PER_QUESTION, Thresholds
from review_store import PENDING, ReviewStore, clean_text, now_iso


# --------------------------------------------------------------------------- argument specs

@dataclass(frozen=True)
class ArgSpec:
    kind: str                # "territory_id" | "positive_number"
    required: bool = True
    description: str = ""


def _validate_arg(name: str, spec: ArgSpec, value: Any) -> Any:
    if spec.kind == "territory_id":
        return normalize_territory_id(value)  # raises ValueError on bad input
    if spec.kind == "positive_number":
        if isinstance(value, bool):
            raise ValueError(f"{name} must be a number")
        if isinstance(value, str):
            try:
                value = float(value.replace(",", "").replace("$", "").strip())
            except ValueError:
                raise ValueError(f"{name} must be a number")
        if not isinstance(value, (int, float)) or math.isnan(value) or math.isinf(value):
            raise ValueError(f"{name} must be a finite number")
        if not (0 < value < 1e12):
            raise ValueError(f"{name} must be greater than 0 and less than 1e12")
        return float(value)
    raise ValueError(f"unsupported argument kind for {name}")


@dataclass
class ToolSpec:
    name: str
    purpose: str
    function: Callable[..., dict[str, Any]]
    requires_approval: bool = False
    args: dict[str, ArgSpec] = field(default_factory=dict)
    kind: str = "read_only"   # "read_only" | "draft_only"


TOOLS: dict[str, ToolSpec] = {
    "summarize_plan": ToolSpec(
        "summarize_plan", "Calculate target vs. proposed quota totals.", summarize_plan),
    "find_planning_exceptions": ToolSpec(
        "find_planning_exceptions", "Find planning inputs that meet illustrative review thresholds.",
        find_planning_exceptions),
    "get_territory_detail": ToolSpec(
        "get_territory_detail", "Return evidence for one known territory ID.", get_territory_detail,
        args={"territory_id": ArgSpec("territory_id", True, "Territory ID such as T05")}),
    "check_data_consistency": ToolSpec(
        "check_data_consistency", "Cross-check the project CSVs against each other (informational).",
        check_data_consistency),
    "list_data_gaps": ToolSpec(
        "list_data_gaps", "List information the dataset does not contain before a quota change could be recommended.",
        list_data_gaps, args={"territory_id": ArgSpec("territory_id", False, "Optional territory ID")}),
    "preview_quota_scenario": ToolSpec(
        "preview_quota_scenario", "Hypothetical in-memory what-if for one territory; nothing is applied.",
        preview_quota_scenario,
        args={"territory_id": ArgSpec("territory_id", True), "alternative_quota_usd": ArgSpec("positive_number", True)}),
}

# Tools whose underlying function accepts thresholds (injected by the session, never by the model/user).
_THRESHOLD_AWARE = {"find_planning_exceptions", "get_territory_detail", "check_data_consistency",
                    "list_data_gaps", "preview_quota_scenario"}

PROHIBITED_ACTIONS = [
    "change quota or territory assignments",
    "write to CRM or source-of-truth systems",
    "send recommendations to employees or managers",
    "approve a plan or make employment decisions",
    "rank individual reps for performance, termination, or compensation actions",
    "execute arbitrary code, shell commands, or file operations from user text",
]


def tool_catalog() -> list[dict[str, Any]]:
    """Public, path-free description of the allowlist (used by the UI and optional model adapter)."""
    cat = [{
        "name": t.name, "purpose": t.purpose, "kind": t.kind, "requires_approval": t.requires_approval,
        "args": {k: {"type": v.kind, "required": v.required, "description": v.description} for k, v in t.args.items()},
    } for t in TOOLS.values()]
    cat.append({"name": "create_change_request", "purpose": "Create a DRAFT review request (simulation; never applied).",
                "kind": "draft_only", "requires_approval": True,
                "args": {"territory_id": {"type": "territory_id", "required": True, "description": ""},
                         "suggested_quota_usd": {"type": "positive_number", "required": False, "description": ""},
                         "rationale": {"type": "text", "required": True, "description": ""}}})
    return cat


def validate_tool_args(tool_name: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    spec = TOOLS[tool_name]
    extra = sorted(set(kwargs) - set(spec.args))
    if extra:
        raise ValueError(f"unexpected argument(s): {', '.join(extra)}")
    clean: dict[str, Any] = {}
    for name, a in spec.args.items():
        if name not in kwargs or kwargs[name] in (None, ""):
            if a.required:
                raise ValueError(f"missing required argument: {name}")
            continue
        clean[name] = _validate_arg(name, a, kwargs[name])
    return clean


def execute_read_only_tool(tool_name: str, *, _data_dir: Path | str | None = None,
                           _thresholds: Thresholds = DEFAULT_THRESHOLDS, **kwargs) -> dict[str, Any]:
    """Execute only allowlisted read-only tools. Unknown tools and bad arguments fail closed.

    ``_data_dir`` / ``_thresholds`` are internal context set by the application, not tool
    arguments: they cannot be supplied by a user or model through ``kwargs``.
    """
    if not isinstance(tool_name, str) or tool_name not in TOOLS:
        return {"error": "Tool not allowlisted", "tool_name": str(tool_name)[:60]}
    try:
        clean = validate_tool_args(tool_name, kwargs)
    except ValueError as exc:
        return {"error": "Invalid tool arguments", "tool_name": tool_name, "detail": str(exc)}
    ctx: dict[str, Any] = {"data_dir": _data_dir}
    if tool_name in _THRESHOLD_AWARE:
        ctx["thresholds"] = _thresholds
    try:
        return TOOLS[tool_name].function(**clean, **ctx)
    except DataValidationError as exc:
        return {"error": "Data validation error", "tool_name": tool_name, "detail": str(exc)}
    except TypeError:
        return {"error": "Invalid tool arguments", "tool_name": tool_name}


# --------------------------------------------------------------------------- draft change requests

def create_change_request(action: str = "propose_quota_review", details: dict[str, Any] | None = None, *,
                          territory_id: str | None = None, suggested_quota_usd: float | None = None,
                          rationale: str = "", assumptions: str | list[str] | None = None,
                          evidence: list[str] | None = None, reviewer: str = "",
                          data_dir: Path | str | None = None) -> dict[str, Any]:
    """Create a draft for human review; NEVER performs the requested change.

    Returns a record with ``status: PENDING_HUMAN_APPROVAL`` and ``executed: False``.
    The current proposed quota is looked up deterministically from the CSV, not taken from
    the caller. Persisting the draft to the local demo queue is a separate, explicit step
    (``ReviewStore.save_request``).
    """
    details = dict(details or {})
    territory_id = territory_id or details.get("territory_id")
    if suggested_quota_usd is None:
        suggested_quota_usd = details.get("suggested_quota_usd", details.get("proposed_quota_usd"))
    record: dict[str, Any] = {
        "request_id": "CR-" + uuid.uuid4().hex[:8].upper(),
        "status": PENDING,
        "action": clean_text(action, 60) or "propose_quota_review",
        "details": {k: v for k, v in details.items() if isinstance(v, (str, int, float, bool))},
        "executed": False,
        "applied_to_source_data": False,
        "simulation": True,
        "created_at": now_iso(),
        "message": ("This is a draft request only. It has NOT been applied. An authorized planner must review "
                    "and approve it in the relevant business system."),
    }
    errors: list[str] = []
    if territory_id is not None:
        try:
            tid = normalize_territory_id(territory_id)
            row = next((r for r in load_quota_plan(data_dir) if r["territory_id"] == tid), None)
            if row is None:
                errors.append(f"Unknown territory_id: {tid}")
            else:
                record["territory_id"] = tid
                record["current_proposed_quota_usd"] = round(row["proposed_quota_usd"], 2)
                record["company_target_usd"] = round(row["company_target_usd"], 2)
        except ValueError as exc:
            errors.append(f"Invalid territory_id: {exc}")
    if suggested_quota_usd not in (None, ""):
        try:
            record["suggested_quota_usd"] = round(_validate_arg("suggested_quota_usd", ArgSpec("positive_number"), suggested_quota_usd), 2)
        except ValueError as exc:
            errors.append(str(exc))
    else:
        record["suggested_quota_usd"] = None
    if isinstance(assumptions, str):
        assumptions = [a.strip() for a in assumptions.splitlines() if a.strip()]
    record["rationale"] = clean_text(rationale)
    record["assumptions"] = [clean_text(a, 300) for a in (assumptions or [])][:20]
    record["evidence"] = [clean_text(e, 300) for e in (evidence or [])][:20]
    record["reviewer"] = clean_text(reviewer, 80)
    if errors:
        record["status"] = "INVALID_DRAFT"
        record["errors"] = errors
    return record


def evidence_for_territory(territory_id: str, data_dir=None, thresholds: Thresholds = DEFAULT_THRESHOLDS) -> list[str]:
    """Deterministic evidence lines pulled from tool output, for pre-filling a draft."""
    d = get_territory_detail(territory_id, data_dir=data_dir, thresholds=thresholds)
    if "error" in d:
        return []
    lines = [f"Target {money(d['company_target_usd'])}; proposed quota {money(d['proposed_quota_usd'])}; "
             f"variance {money(d['variance_usd'])} ({d['variance_pct']:+.2f}%)."]
    if d["quota_per_rep_usd"] is not None:
        lines.append(f"Quota per rep {money(d['quota_per_rep_usd'])} vs historical revenue per rep "
                     f"{money(d['historical_revenue_per_rep_usd'])}.")
    lines.append(f"Opportunity index {d['opportunity_index']:g}.")
    lines += [f"Rule triggered: {r}" for r in d["reasons"]]
    return lines


# --------------------------------------------------------------------------- bounded session

class ToolSession:
    """One user question = one session. Enforces the allowlist, argument validation, and call limit."""

    def __init__(self, max_calls: int = MAX_TOOL_CALLS_PER_QUESTION, data_dir: Path | str | None = None,
                 thresholds: Thresholds = DEFAULT_THRESHOLDS, store: ReviewStore | None = None,
                 audit: bool = True):
        self.max_calls = max_calls
        self.data_dir = data_dir
        self.thresholds = thresholds
        self.store = store
        self.audit = audit and store is not None
        self.calls: list[dict[str, Any]] = []

    @property
    def calls_remaining(self) -> int:
        return max(0, self.max_calls - len(self.calls))

    def call(self, tool_name: str, **kwargs) -> dict[str, Any]:
        safe_args = _safe_args(kwargs)
        if len(self.calls) >= self.max_calls:
            result = {"error": "Tool-call limit reached", "tool_name": str(tool_name)[:60],
                      "detail": f"Maximum {self.max_calls} tool calls per question."}
            self._record(tool_name, safe_args, "rejected_limit")
            return result
        if tool_name == "create_change_request":
            allowed = {"territory_id", "suggested_quota_usd", "rationale", "assumptions", "evidence", "reviewer"}
            extra = set(kwargs) - allowed
            if extra:
                result = {"error": "Invalid tool arguments", "tool_name": tool_name,
                          "detail": f"unexpected argument(s): {', '.join(sorted(extra))}"}
            else:
                result = create_change_request(data_dir=self.data_dir, **kwargs)
        else:
            result = execute_read_only_tool(tool_name, _data_dir=self.data_dir, _thresholds=self.thresholds, **kwargs)
        status = "ok" if "error" not in result and result.get("status") != "INVALID_DRAFT" else "error"
        if result.get("error") == "Tool not allowlisted":
            status = "rejected_not_allowlisted"
        self._record(tool_name, safe_args, status)
        return result

    def _record(self, tool_name: str, safe_args: dict[str, Any], status: str) -> None:
        entry = {"tool": str(tool_name)[:60], "args": safe_args, "status": status, "timestamp": now_iso()}
        self.calls.append(entry)
        if self.audit:
            self.store.append_audit("tool_call", tool=entry["tool"], args=safe_args, status=status)


def _safe_args(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Log-safe view of arguments: IDs and numbers only; free text is summarized by length."""
    out: dict[str, Any] = {}
    for k, v in list(kwargs.items())[:10]:
        key = str(k)[:40]
        if key == "territory_id" and isinstance(v, str):
            out[key] = v.strip().upper()[:12]
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            out[key] = v
        elif v is None:
            out[key] = None
        else:
            out[key] = f"<{type(v).__name__}, {len(str(v))} chars>"
    return out


if __name__ == "__main__":
    print("Available read-only tools:", ", ".join(TOOLS))
    print("Prohibited actions:", "; ".join(PROHIBITED_ACTIONS))
    print("Example approval request:", create_change_request("propose_quota_change", {"territory_id": "T05", "proposed_quota_usd": 12000000}))
