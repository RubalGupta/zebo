"""Generate the portfolio case study (Markdown) from the template structure.

Figures come from the tools at generation time. Test outcomes are included ONLY
if they were actually recorded by a run (Evaluation Lab / unit-test runner);
otherwise the section says they have not been run.
"""
from __future__ import annotations

from typing import Any

from analysis_tools import find_planning_exceptions, load_csv, money, signed_money, summarize_plan
from review_store import ReviewStore


def build_case_study(store: ReviewStore | None = None, data_dir=None) -> str:
    s = summarize_plan(data_dir)
    e = find_planning_exceptions(data_dir)
    eval_run = store.last_eval_run() if store else None
    test_run = store._read_json("last_test_run.json", None) if store else None
    n_reps = len(load_csv("sales_reps.csv", data_dir))
    n_accts = len(load_csv("accounts.csv", data_dir))
    ids = ", ".join(x["territory_id"] for x in e["exceptions"]) or "none"

    if eval_run:
        sm = eval_run["summary"]
        eval_line = (f"Evaluation Lab run at {sm['run_at']} (UTC): {sm['automated_passed']}/{sm['automated_total']} "
                     f"automated cases passed, {sm['automated_failed']} failed; {sm['manual_pending']} manual case(s) "
                     f"still require human evaluation, {sm['manual_recorded']} manual result(s) recorded.")
    else:
        eval_line = "Not yet run in this environment — open the Evaluation Lab and select “Run automated checks”."
    if test_run:
        test_line = (f"Unit test suite run at {test_run['run_at']} (UTC): {test_run['passed']} passed, "
                     f"{test_run['failed']} failed, {test_run['errors']} errors, {test_run['skipped']} skipped "
                     f"(of {test_run['total']}).")
    else:
        test_line = "Not yet run in this environment — select “Run unit test suite” in the Evaluation Lab."

    return f"""# Norbae AI Case Study — Annual Sales Planning Advisor

> Synthetic-data portfolio prototype. Norbae AI helps businesses improve complex workflows with practical AI,
> measurable evaluation, and human oversight. No client data, client results, or measured savings are claimed.

## Summary
Norbae AI developed a synthetic-data prototype demonstrating how an AI-assisted workflow can support annual sales
planning by surfacing planning exceptions, explaining trade-offs with traceable evidence, and preparing
review-ready, draft-only change requests for human decision-makers.

## Business challenge
Annual sales planning involves multiple datasets, capacity assumptions, quota targets, and repeated clarification
requests from sales leaders. Errors or unexplained exceptions can slow planning and reduce confidence in proposed
allocations.

## Approach
- Created a fictional sales planning dataset ({s['territory_count']} territories, {n_reps} fictional reps, {n_accts} fictional accounts).
- Used deterministic Python tools (standard library only) for every calculation and exception rule.
- Built a no-LLM baseline advisor: a transparent intent router that calls at most 3 allowlisted, read-only tools
  per question and answers only from tool output, with an automated grounding check on every figure.
- Enforced the tool allowlist, argument validation, and tool-call limit in code, not in a prompt.
- Added an explicit human-approval boundary: change requests are drafts (`PENDING_HUMAN_APPROVAL`, `executed: false`)
  and demo review decisions never modify planning data (verified by SHA-256 fingerprints of the CSVs).
- Defined automated and manual evaluation cases for numerical accuracy, grounding, uncertainty, and safe behavior.

## What the demo shows (calculated from the synthetic CSVs at generation time)
- Company target {money(s['company_target_usd'])} vs. proposed quota total {money(s['proposed_quota_total_usd'])}
  ({signed_money(s['quota_minus_target_usd'])}); within target: {'yes' if s['within_target'] else 'no'}.
- {e['exception_count']} of {e['territories_evaluated']} territories meet at least one illustrative review rule ({ids}).
- Thresholds are illustrative demo thresholds, not industry standards.

## Demo scenarios
1. Summarize company target versus proposed quota total.
2. List territories that cross review thresholds, in a deterministic review order.
3. Explain a selected territory's quota variance and supporting data.
4. Identify information missing before a quota change could be recommended.
5. Draft a proposed change request without executing it; mark it reviewed or return it in a local demo record.
6. Reject unsupported tool calls, invalid arguments, unknown territory IDs, and requests to rank individuals.

## Implemented vs. future work
| Implemented in this prototype | Future work (not implemented) |
|---|---|
| Local dashboard (Python standard library server + static HTML/JS) | Authentication, roles, and access control |
| Deterministic tools with input validation | Integration with a real planning system (would require approvals) |
| No-LLM intent router with grounding check | Validated, business-owned threshold policy |
| Optional local-model adapter (off by default; tested only with a fake model) | Evaluation with a real model and human graders |
| Draft-only change requests and local demo review log | Multi-scenario comparison and capacity modelling |
| Evaluation Lab with automated and manual cases | User testing with authorized participants |

## Results
Only actual, recorded runs are listed here:
- Automated tests: {test_line}
- Evaluation cases: {eval_line}
- Known limitations: synthetic data; illustrative thresholds; rule-based router supports a limited set of question
  patterns; optional model adapter not evaluated against a real model; no authentication; demo review records are
  local JSON files.

## Limitations
This is a prototype using synthetic data and illustrative thresholds. It is not a production planning system, does
not validate the fairness of quota allocations, does not make individual performance judgements, and has not been
shown to generate measured financial savings.

## Next steps
- Add scenario comparison with explicit, owner-approved assumptions.
- Run the manual evaluation cases (TC07–TC10) with a real model and documented human review.
- Conduct user testing with authorized participants and non-sensitive data.
- For a fintech adaptation, replace sales rules with approved, fictional control/policy documents, source
  citations, access controls, and domain-specific evaluation cases — the tool registry, router, review store,
  and Evaluation Lab are designed to be reused.
"""
