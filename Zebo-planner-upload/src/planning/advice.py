"""Move advice and territory purity. Deterministic, explainable scoring — every reason carries its evidence.

A verdict is advice for a human planner, not a decision. Scores use illustrative rules (planning/config.py).
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from planning.analysis import a_an, fmt_money, fmt_pct
from planning.config import COVERAGE_LEVEL, DEFAULT_RULES, LABEL, PlanningRules
from planning.data import PlanningData


def _terr(data: PlanningData, tid: str) -> pd.Series | None:
    t = data.territories[data.territories["territory_id"] == tid]
    return None if t.empty else t.iloc[0]


def territory_load(accts: pd.DataFrame, tid: str) -> dict[str, Any]:
    g = accts[accts["territory_id"] == tid]
    return {"accounts": int(len(g)), "fy26_ytd": float(g["fy26_ytd"].sum()),
            "open_pipeline": float(g["open_pipeline"].sum())}


def evaluate_move(data: PlanningData, accts: pd.DataFrame, account_id: str, to_territory_id: str,
                  rules: PlanningRules = DEFAULT_RULES) -> dict[str, Any]:
    a = accts[accts["account_id"] == account_id]
    if a.empty:
        return {"error": f"Unknown account: {account_id}"}
    a = a.iloc[0]
    src, dst = _terr(data, a["territory_id"]), _terr(data, to_territory_id)
    if dst is None:
        return {"error": f"Unknown territory: {to_territory_id}"}
    if src is not None and src["territory_id"] == dst["territory_id"]:
        return {"error": f"{a['account_name']} is already in {dst['territory_name']}"}
    reasons: list[dict[str, Any]] = []

    def add(factor: str, impact: int, finding: str) -> None:
        reasons.append({"factor": factor, "impact": impact, "finding": finding})

    # 1. Sales-motion fit
    if dst["motion"] == a["motion"]:
        add("Motion fit", +2, f"{a['motion']} account into {a_an(dst['motion'])} territory — same sales motion.")
        if src is not None and src["motion"] != a["motion"]:
            add("Purity", +1, f"Fixes an impurity: it currently sits in {a_an(src['motion'])} territory.")
    else:
        add("Motion fit", -3, f"{a['motion']} account into {a_an(dst['motion'])} territory — mixes sales motions "
                              f"(purity break).")
    # 2. Pipeline continuity
    opps = data.pipeline[data.pipeline["account_id"] == account_id]
    late = opps[(opps["probability"] >= rules.late_stage_probability) & opps["close_period"].isin(rules.near_term_periods)]
    late_amt = float(late["amount_usd"].sum())
    if late_amt >= rules.late_stage_risk_usd:
        add("Pipeline continuity", -2, f"{fmt_money(late_amt)} of late-stage pipeline closes in "
                                       f"{'/'.join(sorted(late['close_period'].unique()))}; changing owner mid-cycle "
                                       f"risks it. Consider moving after close or keeping the current owner as overlay.")
    big = float(opps["amount_usd"].max()) if len(opps) else 0.0
    if big >= rules.large_pipeline_usd:
        add("Big opportunity", 0, f"Largest open opportunity is {fmt_money(big)} — make sure the receiving seller "
                                  f"has capacity and the right specialist support.")
    # 3. Business trend vs coverage change
    yoy = None if pd.isna(a["yoy_pct"]) else float(a["yoy_pct"])
    delta_cov = COVERAGE_LEVEL[dst["motion"]] - (COVERAGE_LEVEL[src["motion"]] if src is not None else 0)
    if yoy is not None and yoy <= rules.declining_yoy_pct:
        if delta_cov < 0:
            add("Trend", +1, f"Revenue is {fmt_pct(yoy)} YoY (YTD); a step down in coverage is reasonable.")
        elif delta_cov > 0:
            add("Trend", -1, f"Revenue is {fmt_pct(yoy)} YoY (YTD); adding coverage needs a clear recovery thesis.")
        else:
            add("Trend", 0, f"Revenue is {fmt_pct(yoy)} YoY (YTD) — flag for an account plan review.")
    elif yoy is not None and yoy >= rules.growing_yoy_pct:
        if delta_cov < 0:
            add("Trend", -1, f"Revenue is growing {fmt_pct(yoy)} YoY (YTD); reducing coverage could slow it.")
        else:
            add("Trend", +1, f"Revenue is growing {fmt_pct(yoy)} YoY (YTD); coverage is maintained or increased.")
    # 4. Market signals (fictional)
    outlook = a.get("fy27_outlook_pct")
    if outlook is not None and not pd.isna(outlook):
        if outlook >= rules.tailwind_outlook_pct and delta_cov < 0:
            add("Market", -1, f"{a['industry']} outlook is {fmt_pct(outlook)} for FY27 (tailwind); less coverage "
                              f"may leave growth on the table.")
        elif outlook <= rules.headwind_outlook_pct and delta_cov > 0:
            add("Market", -1, f"{a['industry']} outlook is {fmt_pct(outlook)} for FY27 (headwind); extra coverage "
                              f"may not pay back.")
        elif outlook >= rules.tailwind_outlook_pct and delta_cov > 0:
            add("Market", +1, f"{a['industry']} outlook is {fmt_pct(outlook)} for FY27 (tailwind) — supports more coverage.")
    if a.get("signal"):
        imp = {"positive": +1 if delta_cov >= 0 else -1, "negative": +1 if delta_cov <= 0 else -1}.get(a.get("impact"), 0)
        add("Account signal", imp, f"Signal: {a['signal']} ({a.get('impact')}, intent {int(a['intent_score']) if not pd.isna(a.get('intent_score')) else 'n/a'}).")
    # 5. Receiving territory load
    lo, hi = rules.capacity(dst["motion"])
    load = territory_load(accts, dst["territory_id"])
    after = load["accounts"] + 1
    if after > hi:
        add("Capacity", -2, f"{dst['territory_name']} would hold {after} accounts; illustrative capacity for "
                            f"{a_an(dst['motion'])} AE is {lo}–{hi}.")
    else:
        add("Capacity", +1 if after <= hi else 0, f"{dst['territory_name']} would hold {after} accounts "
                                                   f"(capacity guide {lo}–{hi}).")
    # 6. Organisational scope
    if src is not None and src["area_id"] != dst["area_id"]:
        add("Approval", -1, f"Cross-area move ({data.name_of(src['area_id'])} → {data.name_of(dst['area_id'])}) — "
                            f"both managers must sign off.")

    score = sum(r["impact"] for r in reasons)
    verdict = ("Recommended" if score >= rules.recommend_score else
               "Not recommended" if score <= rules.reject_score else "Review with caution")
    return {
        "account_id": account_id, "account_name": a["account_name"], "account_motion": a["motion"],
        "from_territory_id": None if src is None else src["territory_id"],
        "from_territory_name": None if src is None else src["territory_name"],
        "from_owner_id": a["owner_id"], "from_owner": a["owner_name"],
        "to_territory_id": dst["territory_id"], "to_territory_name": dst["territory_name"],
        "to_motion": dst["motion"], "to_owner_id": dst["owner_id"], "to_owner": data.name_of(dst["owner_id"]),
        "fy26_ytd": float(a["fy26_ytd"]), "yoy_pct": yoy, "open_pipeline": float(a["open_pipeline"]),
        "verdict": verdict, "score": int(score), "reasons": reasons, "rules_label": LABEL,
        "note": "Advice only — nothing changes until a planner applies it to the draft plan.",
    }


def suggest_targets(data: PlanningData, accts: pd.DataFrame, account_id: str, allowed_territories: list[str],
                    rules: PlanningRules = DEFAULT_RULES, top: int = 3) -> list[dict[str, Any]]:
    a = accts[accts["account_id"] == account_id]
    if a.empty:
        return []
    cur = a.iloc[0]["territory_id"]
    evals = [evaluate_move(data, accts, account_id, t, rules) for t in allowed_territories if t != cur]
    evals = [e for e in evals if "error" not in e]
    evals.sort(key=lambda e: (-e["score"], e["to_territory_id"]))
    return evals[:top]


def purity_report(data: PlanningData, accts: pd.DataFrame, territories: pd.DataFrame) -> dict[str, Any]:
    """Purity = every account in a territory shares the territory's sales motion."""
    rows, misfits = [], []
    for t in territories.itertuples():
        g = accts[accts["territory_id"] == t.territory_id]
        if g.empty:
            rows.append({"territory_id": t.territory_id, "territory_name": t.territory_name, "motion": t.motion,
                         "owner": data.name_of(t.owner_id), "accounts": 0, "matching": 0, "purity_pct": None,
                         "revenue_purity_pct": None, "status": "Empty", "misfit_motions": ""})
            continue
        match = g[g["motion"] == t.motion]
        rev = g["fy26_ytd"].sum()
        mis = g[g["motion"] != t.motion]
        status = "Pure" if mis.empty else ("Critical" if (mis["motion"] == "Deep").any() and t.motion == "Greenfield"
                                            else "Mixed")
        rows.append({"territory_id": t.territory_id, "territory_name": t.territory_name, "motion": t.motion,
                     "owner": data.name_of(t.owner_id), "accounts": int(len(g)), "matching": int(len(match)),
                     "purity_pct": round(len(match) / len(g) * 100, 1),
                     "revenue_purity_pct": round(match["fy26_ytd"].sum() / rev * 100, 1) if rev > 0 else None,
                     "status": status, "misfit_motions": ", ".join(sorted(mis["motion"].unique()))})
        for m in mis.itertuples():
            misfits.append({"account_id": m.account_id, "account_name": m.account_name, "account_motion": m.motion,
                            "territory_id": t.territory_id, "territory_name": t.territory_name,
                            "territory_motion": t.motion, "fy26_ytd": float(m.fy26_ytd), "area_id": m.area_id})
    df = pd.DataFrame(rows)
    order = {"Critical": 0, "Mixed": 1, "Empty": 2, "Pure": 3}
    rows = sorted(rows, key=lambda r: (order[r["status"]], -(r["accounts"] or 0)))
    return {"territories": rows, "misfits": sorted(misfits, key=lambda m: -m["fy26_ytd"]),
            "pure": int((df["status"] == "Pure").sum()) if len(df) else 0,
            "mixed": int((df["status"] == "Mixed").sum()) if len(df) else 0,
            "critical": int((df["status"] == "Critical").sum()) if len(df) else 0,
            "empty": int((df["status"] == "Empty").sum()) if len(df) else 0,
            "rules_label": LABEL}


def purity_fix(data: PlanningData, accts: pd.DataFrame, misfit: dict[str, Any],
               rules: PlanningRules = DEFAULT_RULES) -> dict[str, Any] | None:
    """Best same-area territory whose motion matches the account (least loaded first), with its evaluation."""
    cands = data.territories[(data.territories["area_id"] == misfit["area_id"]) &
                             (data.territories["motion"] == misfit["account_motion"])]
    if cands.empty:
        return None
    best = sorted(cands["territory_id"], key=lambda t: territory_load(accts, t)["accounts"])[0]
    return evaluate_move(data, accts, misfit["account_id"], best, rules)
