"""No-LLM baseline: a transparent, rule-based intent router.

No language model is involved. The router matches simple keyword rules to an
intent, calls at most 3 allowlisted tools through a ``ToolSession``, and builds
an answer ONLY from those tool results. Every dollar and percentage figure in
an answer is checked against the tool outputs (``grounding_check``).

The router is deliberately conservative: when it does not recognise a request,
it says so instead of guessing.
"""
from __future__ import annotations

import re
from typing import Any

from agent_blueprint import ToolSession, evidence_for_territory
from analysis_tools import money, pct, signed_money
from config import DEFAULT_THRESHOLDS, THRESHOLD_LABEL, Thresholds
from review_store import ReviewStore

MODE = "no_llm_baseline"
MODE_LABEL = "No-LLM baseline (rule-based intent router + deterministic tools). No language model is connected."

FALLBACK_TEXT = ("I can summarize the plan, list planning exceptions, inspect a territory, or draft a review "
                 "request. I can't infer unsupported business context.")

EXAMPLE_QUESTIONS = [
    "Summarize the plan and tell me whether proposed quotas match the company target.",
    "Which territories should we review first, and why?",
    "Show me the details for territory T05.",
    "What information is missing before I can recommend a quota change?",
    "Draft a proposed review request for territory T05, but do not apply it.",
]

BASELINE_NOTE = "Answered by the no-LLM baseline: rule-based routing plus deterministic tools. No language model was used."

# --------------------------------------------------------------------------- patterns
_TERRITORY_RE = re.compile(r"\b(T\d{1,4})\b", re.I)
_TERRITORY_WORD_RE = re.compile(r"\bterritory\s+(?:id\s+)?([A-Za-z0-9_-]*\d[A-Za-z0-9_-]*)\b", re.I)

_SENSITIVE_STRONG = re.compile(
    r"\b(fire|fired|firing|terminat\w*|lay\s?off\w*|let\s+go|dismiss\w*|performance improvement plan|"
    r"disciplin\w*|demot\w*|put\s+on\s+a?\s*pip)\b", re.I)
_SENSITIVE_RANK = re.compile(r"\b(rank\w*|worst|bottom|underperform\w*|low\s+performer\w*|top\s+performer\w*|"
                             r"best|weakest|performance\s+review|compensation|bonus\w*|promot\w*|cut)\b", re.I)
_PERSON = re.compile(r"\b(reps?|employees?|people|person|sales\s*people|salesperson|sellers?|staff|individuals?|"
                     r"r\d{3})\b", re.I)
_CODE = re.compile(r"\b(execute|run|eval)\b.*\b(code|command|script|shell|sql|python)\b|rm\s+-rf|import\s+os|"
                   r"__import__|subprocess|drop\s+table", re.I)
_WRITE = re.compile(r"\b(apply|update|change|set|push|write|commit|approve|email|send|delete|modify|increase|"
                    r"decrease|lower|raise|reduce|overwrite|save)\b", re.I)
_WRITE_TARGET = re.compile(r"\b(quota\w*|territor\w*|crm|plan|record|salesforce|database|system)\b", re.I)
_DRAFT = re.compile(r"\b(draft|change\s+request|review\s+request|proposed\s+review|propose\s+a\s+change)\b", re.I)
_NO_APPLY = re.compile(r"\b(do\s+not|don't|dont|without)\s+(apply|applying|execute|change)\b", re.I)
_SCENARIO = re.compile(r"\b(what\s*-?\s*if|scenario|hypothetical\w*|preview|simulate)\b", re.I)
_MISSING = re.compile(r"\b(missing|what\s+information|what\s+data|insufficient|before\s+i\s+can|gaps?|"
                      r"what\s+else\s+do\s+i\s+need|data\s+quality|reconcil\w*|consisten\w*|need\s+to\s+know)\b", re.I)
_PRIORITY = re.compile(r"\b(review\s+first|prioriti\w*|exceptions?|flag\w*|which\s+territories|risk\w*|outliers?|"
                       r"concerns?|attention|issues?|problems?|look\s+at\s+first)\b", re.I)
_SUMMARY = re.compile(r"\b(summar\w*|overview|totals?|target|match\w*|gap|plan\s+status|big\s+picture|"
                      r"how\s+does\s+the\s+plan)\b", re.I)
_HELP = re.compile(r"\b(what\s+can\s+you\s+do|help|capabilit\w*|how\s+do\s+i\s+use)\b", re.I)
_AMOUNT = re.compile(r"\$?\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(m\b|mm\b|million\b|k\b|thousand\b)?", re.I)


def extract_territory_ids(text: str) -> list[str]:
    ids: list[str] = []
    for m in list(_TERRITORY_RE.finditer(text)) + list(_TERRITORY_WORD_RE.finditer(text)):
        tid = m.group(1).upper()
        if tid not in ids:
            ids.append(tid)
    return ids


def extract_amount(text: str) -> float | None:
    """Parse an explicit dollar amount such as '$13.5M', '13,650,000' or '13.65 million'.

    Territory IDs (T05) and small bare numbers are ignored so they are not mistaken for quotas.
    """
    scrubbed = _TERRITORY_RE.sub(" ", text)
    best = None
    for m in _AMOUNT.finditer(scrubbed):
        raw, unit = m.group(1), (m.group(2) or "").lower()
        value = float(raw.replace(",", ""))
        if unit in ("m", "mm", "million"):
            value *= 1_000_000
        elif unit in ("k", "thousand"):
            value *= 1_000
        has_dollar = "$" in m.group(0)
        if value >= 10_000 and (has_dollar or unit or "," in raw or value >= 100_000):
            best = value
    return best


def classify(question: str) -> str:
    q = question or ""
    if not q.strip():
        return "empty"
    if _SENSITIVE_STRONG.search(q) or (_SENSITIVE_RANK.search(q) and _PERSON.search(q)):
        return "sensitive_individual_decision"
    if _CODE.search(q):
        return "blocked_action"
    if _DRAFT.search(q):
        return "draft_change_request"
    if _SCENARIO.search(q) and not _NO_APPLY.search(q):
        return "scenario_preview"
    if _WRITE.search(q) and _WRITE_TARGET.search(q) and not _NO_APPLY.search(q) and not _MISSING.search(q):
        return "blocked_action"
    if _MISSING.search(q):
        return "missing_information"
    if extract_territory_ids(q):
        return "territory_detail"
    if _PRIORITY.search(q):
        return "prioritize_exceptions"
    if _SUMMARY.search(q):
        return "plan_summary"
    if _HELP.search(q):
        return "capabilities"
    return "fallback"


# --------------------------------------------------------------------------- main entry

def answer_question(question: str, *, data_dir=None, thresholds: Thresholds = DEFAULT_THRESHOLDS,
                    store: ReviewStore | None = None, session: ToolSession | None = None) -> dict[str, Any]:
    question = (question or "")[:2000]
    session = session or ToolSession(data_dir=data_dir, thresholds=thresholds, store=store)
    intent = classify(question)
    handler = _HANDLERS.get(intent, _fallback)
    body, extras = handler(question, session, thresholds)
    evidence = [{"tool": c["tool"], "args": c["args"], "status": c["status"]} for c in session.calls]
    response = {
        "mode": MODE,
        "mode_label": MODE_LABEL,
        "intent": intent,
        "answer": body,
        "tool_calls": session.calls,
        "tool_results": extras.pop("_results", []),
        "evidence_summary": evidence,
        "note": BASELINE_NOTE,
        **extras,
    }
    response["grounding"] = grounding_check(body, response["tool_results"], thresholds)
    if store is not None:
        store.append_audit("advisor_question", mode=MODE, intent=intent, tool_count=len(session.calls),
                           grounded=response["grounding"]["all_traceable"])
    return response


# --------------------------------------------------------------------------- handlers

def _plan_summary(q, s: ToolSession, t: Thresholds):
    summary = s.call("summarize_plan")
    if "error" in summary:
        return _tool_error(summary), {"_results": [summary]}
    exc = s.call("find_planning_exceptions")
    results = [summary, exc]
    match = summary["within_target"]
    lines = [
        f"**Plan summary — {summary['territory_count']} territories (synthetic demo data)**",
        f"- Company target: {money(summary['company_target_usd'])}",
        f"- Proposed quota total: {money(summary['proposed_quota_total_usd'])}",
        f"- Proposed minus target: {signed_money(summary['quota_minus_target_usd'])}"
        + (f" ({pct(summary['quota_minus_target_pct'])} vs target)" if summary.get("quota_minus_target_pct") is not None else ""),
        "",
        f"**Do proposed quotas match the company target? {'Yes' if match else 'No'}.** {summary['interpretation']}",
    ]
    if "error" not in exc:
        ids = ", ".join(e["territory_id"] for e in exc["exceptions"]) or "none"
        lines.append(f"- Territories meeting at least one illustrative review rule: {exc['exception_count']} ({ids}).")
    lines += ["", "Next step: ask “Which territories should we review first, and why?” to see the evidence.",
              f"_{THRESHOLD_LABEL}_"]
    return "\n".join(lines), {"_results": results}


def _prioritize(q, s: ToolSession, t: Thresholds):
    exc = s.call("find_planning_exceptions")
    if "error" in exc:
        return _tool_error(exc), {"_results": [exc]}
    if not exc["exceptions"]:
        return (f"No territory meets an illustrative review rule ({exc['territories_evaluated']} evaluated). "
                f"That does not mean the plan is correct — only that no rule was triggered.\n\n_{THRESHOLD_LABEL}_"), {"_results": [exc]}
    lines = [f"**{exc['exception_count']} of {exc['territories_evaluated']} territories meet at least one illustrative "
             f"review rule.** Suggested review order (sorted by number of rules triggered, then absolute dollar variance):", ""]
    for i, e in enumerate(exc["exceptions"], 1):
        lines.append(f"{i}. **{e['territory_id']}** — target {money(e['company_target_usd'])}, proposed "
                     f"{money(e['proposed_quota_usd'])}, variance {signed_money(e['variance_usd'])} ({pct(e['variance_pct'])}).")
        for r in e["reasons"]:
            lines.append(f"   - Why: {r}")
        if e["variance_pct"] is not None and abs(abs(e["variance_pct"]) - t.variance_pct) < 0.005:
            lines.append("   - Note: this territory sits exactly on the variance threshold; a small threshold "
                         "change would change this flag.")
    triggered = {f for e in exc["exceptions"] for f in e["flags"]}
    untriggered = [r for r in ("quota_per_rep_vs_historical", "opportunity_index_out_of_range", "invalid_capacity")
                   if r not in triggered]
    if untriggered:
        lines.append("")
        lines.append("No territory triggered: " + ", ".join(untriggered).replace("_", " ") + ".")
    lines += ["", "A flag means “review this input”, not “this quota is wrong”. Business context (e.g. new hires, "
              "market changes) is not in the dataset.", f"_{THRESHOLD_LABEL}_"]
    return "\n".join(lines), {"_results": [exc]}


def _territory_detail(q, s: ToolSession, t: Thresholds):
    ids = extract_territory_ids(q)
    results, sections = [], []
    for tid in ids:
        d = s.call("get_territory_detail", territory_id=tid)
        if d.get("error") == "Tool-call limit reached":
            sections.append(f"Stopped: the limit of {s.max_calls} tool calls per question was reached. "
                            f"Ask about {tid} separately.")
            break
        results.append(d)
        if "error" in d:
            valid = ", ".join(d.get("valid_ids", []))
            sections.append(f"**{tid}:** {d['error']}. I won't invent data for it." + (f" Valid IDs: {valid}." if valid else ""))
            continue
        sections.append(_format_detail(d))
    return "\n\n".join(sections), {"_results": results}


def _format_detail(d: dict[str, Any]) -> str:
    head = f"**{d['territory_id']}"
    if d.get("territory_name"):
        head += f" — {d['territory_name']} ({d.get('region', '')})"
    head += "**"
    lines = [head,
             f"- Company target: {money(d['company_target_usd'])}; proposed quota: {money(d['proposed_quota_usd'])}",
             f"- Variance: {signed_money(d['variance_usd'])} ({pct(d['variance_pct'])})",
             f"- Rep capacity: {d['rep_capacity']:g}; quota per rep: {money(d['quota_per_rep_usd'])}; "
             f"historical revenue per rep: {money(d['historical_revenue_per_rep_usd'])}",
             f"- Opportunity index: {d['opportunity_index']:.2f}"]
    if d["reasons"]:
        lines.append("- Rules triggered: " + " ".join(d["reasons"]))
    else:
        lines.append("- Rules triggered: none.")
    p = d.get("pipeline_sample")
    if p:
        lines.append(f"- Pipeline sample: {p['account_count']} synthetic accounts, {money(p['total_opportunity_usd'])} "
                     "total (a sample, not the full pipeline).")
    for n in d.get("data_notes", []):
        lines.append(f"- Data note: {n}")
    lines.append(f"\n{d['caveat']}")
    return "\n".join(lines)


def _missing_info(q, s: ToolSession, t: Thresholds):
    ids = extract_territory_ids(q)
    gaps = s.call("list_data_gaps", **({"territory_id": ids[0]} if ids else {}))
    if "error" in gaps:
        return _tool_error(gaps), {"_results": [gaps]}
    scope = f" for {gaps['territory_id']}" if gaps.get("territory_id") else ""
    lines = [f"**I can't recommend a specific quota change{scope} from this dataset.** {gaps['conclusion']}", "",
             "**Not in the dataset:**"]
    lines += [f"- {g}" for g in gaps["not_in_dataset"]]
    f = gaps["data_quality_findings"]
    if f:
        lines += ["", f"**Data-quality findings to resolve ({len(f)}):**"]
        for item in f[:6]:
            lines.append(f"- {item['territory_id']}: {item['detail']}")
        if len(f) > 6:
            lines.append(f"- …and {len(f) - 6} more (see Planning Exceptions → data checks).")
    lines += ["", "A planner or business owner should supply this context; I won't assume it."]
    return "\n".join(lines), {"_results": [gaps]}


def _draft(q, s: ToolSession, t: Thresholds):
    ids = extract_territory_ids(q)
    if not ids:
        return ("Which territory should the draft review request cover? Please include a territory ID such as T05. "
                "No draft was created."), {"_results": []}
    tid = ids[0]
    detail = s.call("get_territory_detail", territory_id=tid)
    if "error" in detail:
        return f"{detail['error']}. No draft was created.", {"_results": [detail]}
    amount = extract_amount(q)
    results = [detail]
    preview = None
    if amount:
        preview = s.call("preview_quota_scenario", territory_id=tid, alternative_quota_usd=amount)
        results.append(preview)
    rationale = ("Territory met illustrative review rule(s): " + " ".join(detail["reasons"])) if detail["reasons"] else \
        "Planner requested a review; no illustrative rule was triggered for this territory."
    draft = s.call("create_change_request", territory_id=tid, suggested_quota_usd=amount,
                   rationale=rationale + " [Auto-drafted from tool output — a planner must confirm the business rationale.]",
                   assumptions=["Synthetic demo data", "Thresholds are illustrative and not business-validated",
                                "Business context for the proposed quota is not in the dataset"],
                   evidence=evidence_for_territory(tid, data_dir=s.data_dir, thresholds=t))
    results.append(draft)
    lines = [f"**Draft review request created for {tid} — NOT applied.**",
             f"- Status: `{draft['status']}`; executed: `{str(draft['executed']).lower()}`; request ID {draft['request_id']}",
             f"- Current proposed quota: {money(draft.get('current_proposed_quota_usd'))} "
             f"(target {money(draft.get('company_target_usd'))})",
             f"- Suggested alternative quota: {money(draft['suggested_quota_usd']) if draft.get('suggested_quota_usd') else 'none provided'}",
             f"- Rationale (auto-drafted, needs planner confirmation): {rationale}"]
    if preview and "error" not in preview:
        lines.append(f"- What-if (hypothetical): plan total would be {money(preview['plan_total_under_alternative_usd'])}, "
                     f"gap vs target {signed_money(preview['plan_gap_under_alternative_usd'])}.")
    lines += ["", "Nothing in the quota plan, territory data, or any business system has changed. Use **Save to review "
              "queue** to keep this draft in the local demo queue, where it can be marked reviewed or returned for revision."]
    return "\n".join(lines), {"_results": results, "draft": draft}


def _scenario(q, s: ToolSession, t: Thresholds):
    ids = extract_territory_ids(q)
    amount = extract_amount(q)
    if not ids or not amount:
        return ("For a what-if, give a territory ID and an alternative quota, e.g. “What if T05 were [amount]?”. "
                "Nothing was calculated."), {"_results": []}
    p = s.call("preview_quota_scenario", territory_id=ids[0], alternative_quota_usd=amount)
    if "error" in p:
        return _tool_error(p), {"_results": [p]}
    flags = ", ".join(p["flags_under_alternative"]) or "none"
    lines = [f"**Hypothetical what-if for {p['territory_id']} — not applied.**",
             f"- Current proposed quota: {money(p['current_proposed_quota_usd'])} → alternative {money(p['alternative_quota_usd'])} "
             f"({signed_money(p['territory_change_usd'])})",
             f"- Variance vs territory target under the alternative: {pct(p['alternative_variance_pct'])}; rules triggered: {flags}",
             f"- Plan total: {money(p['plan_total_current_usd'])} → {money(p['plan_total_under_alternative_usd'])}",
             f"- Plan gap vs company target: {signed_money(p['plan_gap_current_usd'])} → {signed_money(p['plan_gap_under_alternative_usd'])}",
             "", p["note"]]
    return "\n".join(lines), {"_results": [p]}


def _sensitive(q, s, t):
    return ("I can't rank individual reps or support termination, discipline, compensation, or individual performance "
            "decisions. This prototype is limited to aggregate territory-level planning inputs, and those decisions "
            "need appropriate human, HR, and legal processes.\n\nI can help with: the plan summary, territory-level "
            "exceptions, a territory's aggregate evidence, or a draft review request for human review."), {"_results": []}


def _blocked(q, s, t):
    return ("I can't change quotas, territories, CRM records, or any business system, and I can't run code or commands. "
            "This prototype is read-only.\n\nWhat I can do: show the evidence, run a hypothetical what-if (give a territory ID "
            "and an alternative quota amount), or create a draft review request that stays `PENDING_HUMAN_APPROVAL` "
            "and is never applied."), {"_results": []}


def _capabilities(q, s, t):
    return (FALLBACK_TEXT + "\n\nTry:\n" + "\n".join(f"- {e}" for e in EXAMPLE_QUESTIONS)), {"_results": []}


def _fallback(q, s, t):
    return (f"I didn't recognise that request, so I haven't looked anything up. {FALLBACK_TEXT}\n\n"
            "Examples:\n" + "\n".join(f"- {e}" for e in EXAMPLE_QUESTIONS)), {"_results": []}


def _empty(q, s, t):
    return "Please type a question. " + FALLBACK_TEXT, {"_results": []}


def _tool_error(r: dict[str, Any]) -> str:
    detail = r.get("detail") or r.get("error")
    return f"I couldn't complete that: {detail}. No figures were invented."


_HANDLERS = {
    "plan_summary": _plan_summary,
    "prioritize_exceptions": _prioritize,
    "territory_detail": _territory_detail,
    "missing_information": _missing_info,
    "draft_change_request": _draft,
    "scenario_preview": _scenario,
    "sensitive_individual_decision": _sensitive,
    "blocked_action": _blocked,
    "capabilities": _capabilities,
    "fallback": _fallback,
    "empty": _empty,
}


# --------------------------------------------------------------------------- grounding check

_MONEY_IN_TEXT = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)")
_PCT_IN_TEXT = re.compile(r"([+-]?\d+(?:\.\d+)?)%")
_NUM_IN_TEXT = re.compile(r"[+-]?\d[\d,]*(?:\.\d+)?")


def _collect_numbers(obj: Any, out: set[float]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.add(float(obj)); out.add(abs(float(obj)))
    elif isinstance(obj, str):
        for m in _NUM_IN_TEXT.finditer(obj):
            try:
                v = float(m.group(0).replace(",", ""))
                out.add(v); out.add(abs(v))
            except ValueError:
                pass
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_numbers(v, out)


def grounding_check(answer: str, tool_results: list[dict[str, Any]], thresholds: Thresholds = DEFAULT_THRESHOLDS) -> dict[str, Any]:
    """Verify every $ and % figure in the answer appears in (or rounds from) the tool outputs."""
    known: set[float] = set()
    _collect_numbers(tool_results, known)
    _collect_numbers(thresholds.to_dict(), known)
    checked, untraceable = 0, []
    for m in _MONEY_IN_TEXT.finditer(answer):
        checked += 1
        v = float(m.group(1).replace(",", ""))
        if not any(abs(v - k) <= 0.5 for k in known):
            untraceable.append(m.group(0))
    for m in _PCT_IN_TEXT.finditer(answer):
        checked += 1
        v = abs(float(m.group(1)))
        if not any(abs(v - k) <= 0.006 for k in known):
            untraceable.append(m.group(0))
    return {"figures_checked": checked, "untraceable": untraceable, "all_traceable": not untraceable}
