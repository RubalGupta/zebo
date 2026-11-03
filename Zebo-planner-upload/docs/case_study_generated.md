# Norbae AI Case Study — Annual Sales Planning Advisor

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
- Created a fictional sales planning dataset (10 territories, 100 fictional reps, 60 fictional accounts).
- Used deterministic Python tools (standard library only) for every calculation and exception rule.
- Built a no-LLM baseline advisor: a transparent intent router that calls at most 3 allowlisted, read-only tools
  per question and answers only from tool output, with an automated grounding check on every figure.
- Enforced the tool allowlist, argument validation, and tool-call limit in code, not in a prompt.
- Added an explicit human-approval boundary: change requests are drafts (`PENDING_HUMAN_APPROVAL`, `executed: false`)
  and demo review decisions never modify planning data (verified by SHA-256 fingerprints of the CSVs).
- Defined automated and manual evaluation cases for numerical accuracy, grounding, uncertainty, and safe behavior.

## What the demo shows (calculated from the synthetic CSVs at generation time)
- Company target $90,000,000 vs. proposed quota total $91,175,000
  (+$1,175,000); within target: no.
- 2 of 10 territories meet at least one illustrative review rule (T05, T10).
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
- Automated tests: Unit test suite run at 2026-10-14T19:07:10+00:00 (UTC): 98 passed, 0 failed, 0 errors, 1 skipped (of 99).
- Evaluation cases: Evaluation Lab run at 2026-10-14T19:07:34+00:00 (UTC): 13/13 automated cases passed, 0 failed; 4 manual case(s) still require human evaluation, 0 manual result(s) recorded.
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
