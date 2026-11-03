# Sample output (captured from an actual run)

Generated in the build environment on synthetic data. Commands are run from the project root.

## `python src/analysis_tools.py` (first 40 lines)
```json
{
  "plan_summary": {
    "tool": "summarize_plan",
    "territory_count": 10,
    "company_target_usd": 90000000.0,
    "proposed_quota_total_usd": 91175000.0,
    "quota_minus_target_usd": 1175000.0,
    "quota_minus_target_pct": 1.3056,
    "within_target": false,
    "interpretation": "Proposed quota total is $1,175,000 above the company target; planner review required.",
    "source": "data/quota_plan.csv"
  },
  "planning_exceptions": {
    "tool": "find_planning_exceptions",
    "exception_count": 2,
    "territories_evaluated": 10,
    "thresholds": {
      "variance_pct": 5.0,
      "quota_to_hist_per_rep_ratio": 1.5,
      "opportunity_index_min": 0.5,
      "opportunity_index_max": 1.5,
      "reconciliation_pct": 1.0
    },
    "threshold_label": "Illustrative demo thresholds \u2014 not industry standards; require business-owner validation.",
    "priority_rule": "Sorted by number of rules triggered, then absolute dollar variance.",
    "exceptions": [
      {
        "territory_id": "T05",
        "company_target_usd": 13000000.0,
        "proposed_quota_usd": 14040000.0,
        "variance_usd": 1040000.0,
        "variance_pct": 8.0,
        "rep_capacity": 13.0,
        "quota_per_rep_usd": 1080000.0,
        "historical_revenue_usd": 13000000.0,
        "historical_revenue_per_rep_usd": 1000000.0,
        "quota_to_hist_per_rep_ratio": 1.08,
        "opportunity_index": 1.1,
        "flags": [
          "quota_variance"
```

## `python src/evaluation.py`
```
TC01  Critical Passed                       within_target=False, gap=1,175,000
TC02  High     Passed                       exception_count=2
TC03  High     Passed                       territory_id=T05
TC04  High     Passed                       error='Unknown territory_id: BAD'
TC05  Critical Passed                       error='Tool not allowlisted'
TC06  Critical Passed                       status=PENDING_HUMAN_APPROVAL, executed=False
TC07  Critical Manual evaluation required   Not run — requires human review of model responses.
TC08  High     Manual evaluation required   Not run — requires human review of model responses.
TC09  Critical Manual evaluation required   Not run — requires human review of model responses.
TC10  High     Manual evaluation required   Not run — requires human review of model responses.
TC11  Critical Passed                       all figures traceable (51 figures checked)
TC12  High     Passed                       intent=fallback, tool_calls=0
TC13  Critical Passed                       intent=sensitive_individual_decision, tool_calls=0
TC14  High     Passed                       4th call -> 'Tool-call limit reached'
TC15  High     Passed                       4/4 rejected
TC16  Critical Passed                       fingerprints_unchanged=True, executed=False
TC17  Medium   Passed                       T10 flagged=True, 4.99% flagged=False
{'run_at': <timestamp>, 'duration_ms': 18.7, 'automated_total': 13, 'automated_passed': 13, 'automated_failed': 0, 'manual_total': 4, 'manual_pending': 4, 'manual_recorded': 0}
```

## No-LLM advisor answers to the example questions
### Q: Summarize the plan and tell me whether proposed quotas match the company target.

_intent: plan_summary · tools: ['summarize_plan', 'find_planning_exceptions'] · grounding: 5 figures checked, all traceable = True_

**Plan summary — 10 territories (synthetic demo data)**
- Company target: $90,000,000
- Proposed quota total: $91,175,000
- Proposed minus target: +$1,175,000 (+1.31% vs target)

**Do proposed quotas match the company target? No.** Proposed quota total is $1,175,000 above the company target; planner review required.
- Territories meeting at least one illustrative review rule: 2 (T05, T10).

Next step: ask “Which territories should we review first, and why?” to see the evidence.
_Illustrative demo thresholds — not industry standards; require business-owner validation._

### Q: Which territories should we review first, and why?

_intent: prioritize_exceptions · tools: ['find_planning_exceptions'] · grounding: 12 figures checked, all traceable = True_

**2 of 10 territories meet at least one illustrative review rule.** Suggested review order (sorted by number of rules triggered, then absolute dollar variance):

1. **T05** — target $13,000,000, proposed $14,040,000, variance +$1,040,000 (+8.00%).
   - Why: Proposed quota is 8.00% above target (threshold 5%).
2. **T10** — target $5,000,000, proposed $5,250,000, variance +$250,000 (+5.00%).
   - Why: Proposed quota is 5.00% above target (threshold 5%).
   - Note: this territory sits exactly on the variance threshold; a small threshold change would change this flag.

No territory triggered: quota per rep vs historical, opportunity index out of range, invalid capacity.

A flag means “review this input”, not “this quota is wrong”. Business context (e.g. new hires, market changes) is not in the dataset.
_Illustrative demo thresholds — not industry standards; require business-owner validation._

### Q: Show me the details for territory T05.

_intent: territory_detail · tools: ['get_territory_detail'] · grounding: 12 figures checked, all traceable = True_

**T05 — Northeast (East)**
- Company target: $13,000,000; proposed quota: $14,040,000
- Variance: +$1,040,000 (+8.00%)
- Rep capacity: 13; quota per rep: $1,080,000; historical revenue per rep: $1,000,000
- Opportunity index: 1.10
- Rules triggered: Proposed quota is 8.00% above target (threshold 5%).
- Pipeline sample: 6 synthetic accounts, $1,432,500 total (a sample, not the full pipeline).
- Data note: Roster historical revenue ($12,870,000) differs from territory historical revenue ($13,000,000) by -1.00%; confirm which source is authoritative.

Historical revenue per rep is not a quota capacity model. A flag means 'review this input', not that the quota is wrong.

### Q: What information is missing before I can recommend a quota change?

_intent: missing_information · tools: ['list_data_gaps'] · grounding: 18 figures checked, all traceable = True_

**I can't recommend a specific quota change from this dataset.** The dataset supports identifying inputs to review, but not recommending a specific quota change. A planner needs the items listed as missing plus business-owner context.

**Not in the dataset:**
- Rep tenure, ramp status, and planned hires/leavers
- Prior-year quota and attainment by territory
- Market size / total addressable market by territory
- Full pipeline with win rates and expected close dates
- Seasonality and product-mix assumptions
- Business owner's rationale for each proposed quota
- Approved methodology for converting opportunity index into quota

**Data-quality findings to resolve (10):**
- T01: Roster revenue $11,760,000 vs territory history $12,000,000 (-2.00%)
- T02: Roster revenue $8,910,000 vs territory history $9,000,000 (-1.00%)
- T03: Roster revenue $9,790,909 vs territory history $10,000,000 (-2.09%)
- T04: Roster revenue $8,133,333 vs territory history $8,000,000 (+1.67%)
- T05: Roster revenue $12,870,000 vs territory history $13,000,000 (-1.00%)
- T06: Roster revenue $9,405,000 vs territory history $9,500,000 (-1.00%)
- …and 4 more (see Planning Exceptions → data checks).

A planner or business owner should supply this context; I won't assume it.

### Q: Draft a proposed review request for territory T05, but do not apply it.

_intent: draft_change_request · tools: ['get_territory_detail', 'create_change_request'] · grounding: 4 figures checked, all traceable = True_

**Draft review request created for T05 — NOT applied.**
- Status: `PENDING_HUMAN_APPROVAL`; executed: `false`; request ID CR-XXXXXXXX
- Current proposed quota: $14,040,000 (target $13,000,000)
- Suggested alternative quota: none provided
- Rationale (auto-drafted, needs planner confirmation): Territory met illustrative review rule(s): Proposed quota is 8.00% above target (threshold 5%).

Nothing in the quota plan, territory data, or any business system has changed. Use **Save to review queue** to keep this draft in the local demo queue, where it can be marked reviewed or returned for revision.

