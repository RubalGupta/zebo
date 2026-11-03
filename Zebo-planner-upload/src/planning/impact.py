"""FY27 revenue impact of territory moves — an explainable, assumption-labelled estimate (not a forecast system).

For each account:
  baseline FY26 (annualized) = Jan–Nov actual ÷ (FY25 Jan–Nov share of FY25 full year)
                               (i.e. December estimated from last year's seasonality; Phase 2 adds the full method)
  FY27 if it stays           = baseline × (1 + industry FY27 outlook)
  FY27 if it moves           = baseline × (1 + outlook + coverage effect)
                               − disruption risk on late-stage pipeline closing in FY26-Dec/FY27-Q1 (owner change)
A move shifts revenue between territories/owners (it nets to zero at BU level); the *company* impact is only the
coverage effect and the disruption risk. Ranges (low/likely/high) flex the outlook and coverage effect.
All factors are illustrative demo assumptions — validate with finance before use.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from planning.analysis import fmt_money, fmt_pct
from planning.config import COVERAGE_LEVEL, DEFAULT_RULES, PlanningRules

COVERAGE_EFFECT_PER_LEVEL = 0.03      # ±3% FY27 revenue per step of coverage (Greenfield→Expansion→Deep)
DISRUPTION_HAIRCUT = 0.15             # share of near-term late-stage pipeline at risk when the owner changes
RANGE_OUTLOOK_PTS = 0.03              # ± 3 points on the industry outlook for low/high
ASSUMPTIONS = [
    "December FY26 estimated from FY25 seasonality (FY25 Jan–Nov share of FY25 total).",
    "FY27 growth = fictional industry outlook for the account's industry.",
    f"Coverage effect: ±{COVERAGE_EFFECT_PER_LEVEL:.0%} of revenue per coverage step (Greenfield→Expansion→Deep).",
    f"Disruption risk: {DISRUPTION_HAIRCUT:.0%} of late-stage pipeline closing FY26-Dec/FY27-Q1 when the owner changes.",
    "Low/high flex the outlook by ±3 points and the coverage effect between 0× and 2×.",
    "Illustrative demo assumptions — not a finance forecast.",
]


def annualized_fy26(row: pd.Series) -> float:
    if row["fy25_total"] > 0 and row["fy25_ytd"] > 0:
        return float(row["fy26_ytd"] / (row["fy25_ytd"] / row["fy25_total"]))
    return float(row["fy26_ytd"] * 12 / 11)


def account_move_impact(data, accts: pd.DataFrame, account_id: str, to_territory_id: str,
                        rules: PlanningRules = DEFAULT_RULES) -> dict[str, Any]:
    a = accts[accts["account_id"] == account_id]
    t_to = data.territories[data.territories["territory_id"] == to_territory_id]
    if a.empty:
        return {"error": f"Unknown account: {account_id}"}
    if t_to.empty:
        return {"error": f"Unknown territory: {to_territory_id}"}
    a, t_to = a.iloc[0], t_to.iloc[0]
    t_from = data.territories[data.territories["territory_id"] == a["territory_id"]].iloc[0]
    base = annualized_fy26(a)
    outlook = float(a["fy27_outlook_pct"]) / 100 if not pd.isna(a.get("fy27_outlook_pct")) else 0.0
    steps = COVERAGE_LEVEL[t_to["motion"]] - COVERAGE_LEVEL[t_from["motion"]]
    owner_changes = t_to["owner_id"] != a["owner_id"]
    opps = data.pipeline[data.pipeline["account_id"] == account_id]
    late = opps[(opps["probability"] >= rules.late_stage_probability) & opps["close_period"].isin(rules.near_term_periods)]
    risk_base = float(late["weighted_usd"].sum()) if owner_changes else 0.0

    def scen(outlook_shift: float, cov_mult: float, risk_mult: float) -> tuple[float, float]:
        stay = base * (1 + outlook + outlook_shift)
        move = base * (1 + outlook + outlook_shift + steps * COVERAGE_EFFECT_PER_LEVEL * cov_mult) \
            - risk_base * DISRUPTION_HAIRCUT * risk_mult
        return stay, move

    stay, move = scen(0, 1, 1)
    lo_stay, lo_move = scen(-RANGE_OUTLOOK_PTS, 0 if steps > 0 else 2, 1.5)
    hi_stay, hi_move = scen(+RANGE_OUTLOOK_PTS, 2 if steps > 0 else 0, 0.5)
    return {
        "account_id": account_id, "account_name": a["account_name"],
        "from_territory_id": t_from["territory_id"], "from_territory_name": t_from["territory_name"],
        "to_territory_id": t_to["territory_id"], "to_territory_name": t_to["territory_name"],
        "from_area_id": t_from["area_id"], "to_area_id": t_to["area_id"],
        "fy26_baseline": base, "industry_outlook_pct": outlook * 100, "coverage_steps": int(steps),
        "late_stage_pipeline_at_risk": risk_base,
        "fy27_if_stay": stay, "fy27_if_move": move, "company_delta": move - stay,
        "company_delta_low": lo_move - lo_stay, "company_delta_high": hi_move - hi_stay,
        "transfer_to_receiving_territory": move,
    }


def moves_impact(data, accts: pd.DataFrame, moves: list[tuple[str, str]],
                 rules: PlanningRules = DEFAULT_RULES) -> dict[str, Any]:
    """Impact of a set of (account_id, to_territory_id) moves, with territory-level shifts and company net."""
    items = [account_move_impact(data, accts, a, t, rules) for a, t in moves]
    errors = [i["error"] for i in items if "error" in i]
    items = [i for i in items if "error" not in i]
    terr: dict[str, float] = {}
    for i in items:
        terr[i["from_territory_id"]] = terr.get(i["from_territory_id"], 0.0) - i["fy27_if_stay"]
        terr[i["to_territory_id"]] = terr.get(i["to_territory_id"], 0.0) + i["fy27_if_move"]
    shifts = sorted(({"territory_id": k, "territory_name": data.name_of(k), "fy27_change": v} for k, v in terr.items()),
                    key=lambda r: r["fy27_change"])
    total = sum(i["company_delta"] for i in items)
    return {
        "items": items, "errors": errors, "territory_shifts": shifts,
        "fy27_moved_revenue": sum(i["fy27_if_move"] for i in items),
        "company_delta": total,
        "company_delta_low": sum(i["company_delta_low"] for i in items),
        "company_delta_high": sum(i["company_delta_high"] for i in items),
        "assumptions": ASSUMPTIONS,
    }


def summary_lines(r: dict[str, Any]) -> list[str]:
    out = []
    for i in r["items"]:
        cov = {1: "one step more coverage", 2: "two steps more coverage", -1: "one step less coverage",
               -2: "two steps less coverage"}.get(i["coverage_steps"], "same coverage level")
        out.append(f"**{i['account_name']}** → {i['to_territory_name']}: FY27 {fmt_money(i['fy27_if_move'])} moves with "
                   f"it (vs {fmt_money(i['fy27_if_stay'])} if it stays); {cov}"
                   + (f", {fmt_money(i['late_stage_pipeline_at_risk'])} weighted late-stage pipeline at risk from the owner change"
                      if i["late_stage_pipeline_at_risk"] else "")
                   + f" → company effect {'+' if i['company_delta'] >= 0 else ''}{fmt_money(i['company_delta'])}.")
    return out
