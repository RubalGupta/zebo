# Norbae AI Case Study — Annual Sales Planning Advisor

> Template. The dashboard's **Case Study** tab (and `GET /api/case-study.md`) fills in the live figures and only the
> test runs that were actually recorded. Keep bracketed fields as-is until real results exist.

## Summary
Norbae AI developed a synthetic-data prototype demonstrating how an AI-assisted workflow can support annual sales
planning by surfacing planning exceptions, explaining trade-offs with traceable evidence, and preparing review-ready,
draft-only change requests for human decision-makers.

## Business challenge
Annual sales planning can involve multiple datasets, capacity assumptions, quota targets, and repeated clarification
requests. Errors or unexplained exceptions can slow planning and reduce confidence in proposed allocations.

## Approach
- Created a fictional sales planning dataset.
- Used deterministic Python tools for calculations and exception detection.
- Built a no-LLM baseline advisor (rule-based intent router) that calls allowlisted read-only tools, max 3 per question.
- Added an explicit human approval boundary for any proposed changes (draft-only, never executed).
- Defined tests for numerical accuracy, evidence grounding, uncertainty, and safe behavior.

## Demo scenarios
1. Summarize company target versus proposed quota total.
2. List territories that cross review thresholds.
3. Explain a selected territory's quota variance and supporting data.
4. Identify information missing before a quota change could be recommended.
5. Draft a proposed change request without executing it.
6. Reject unsupported tool calls, unknown territory IDs, and requests to rank individuals.

## Implemented vs. future work
| Implemented | Future work |
|---|---|
| [list only verified features] | [list planned features] |

## Results
Populate only after running and documenting actual tests:
- Automated tests passed: [actual result]
- Evaluation cases completed: [actual result — distinguish automated vs. manual]
- Known limitations found: [list]
- Changes made after testing: [list]

## Limitations
This is a prototype using synthetic data and illustrative thresholds. It is not a production planning system, does
not validate the fairness of quota allocations, and has not been shown to generate measured financial savings.

## Next steps
- Add scenario comparison with explicit assumptions.
- Run manual evaluation cases with a real model and documented human review.
- Conduct user testing with authorized participants and non-sensitive data.
- For fintech adaptation, replace sales rules with approved control/policy documents, source citations, access
  controls, and domain-specific evaluation cases.
