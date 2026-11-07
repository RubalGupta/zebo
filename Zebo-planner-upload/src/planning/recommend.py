"""'Top actions for you' — ranked, explainable planning recommendations (recommend, don't just display).

Each candidate carries its evidence and a dollar figure "at stake" used only to rank across types. Four kinds:
  rebalance   a seller's book is over the capacity guide for its territory motion          (at stake: book revenue)
  purity      a territory holds accounts of another sales motion and a same-area fix exists (at stake: misfit revenue)
  cover_up    a fast-growing account with big pipeline sits in a lighter-coverage motion    (at stake: open pipeline)
  protect     a sharply declining account still has big open pipeline                       (at stake: open pipeline)
All thresholds are illustrative demo rules (planning/config.py). Advice only: nothing is changed.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from planning.advice import purity_fix, purity_report
from planning.analysis import a_an, fmt_money, fmt_pct
from planning.config import COVERAGE_LEVEL, DEFAULT_RULES, PlanningRules
from planning.data import PlanningData

KIND_LABEL = {"rebalance": "Rebalance an overloaded book", "purity": "Fix a territory's sales-motion mix",
              "cover_up": "Step up coverage on a fast grower", "protect": "Protect a declining account's pipeline"}


def recommend_actions(data: PlanningData, accts: pd.DataFrame, territories: pd.DataFrame,
                      rules: PlanningRules = DEFAULT_RULES, limit: int = 3) -> dict[str, Any]:
    cands: list[dict[str, Any]] = []
    tids = set(territories["territory_id"])
    a = accts[accts["territory_id"].isin(tids)]
    tinfo = territories.set_index("territory_id")

    # 1. overloaded books
    for tid, g in a.groupby("territory_id"):
        t = tinfo.loc[tid]
        lo, hi = rules.capacity(t["motion"])
        if len(g) > hi:
            owner = data.name_of(t["owner_id"])
            cands.append({
                "kind": "rebalance", "title": f"Rebalance {owner}'s book",
                "why": f"{len(g)} accounts vs a {lo}–{hi} guide for {t['motion']} coverage in {t['territory_name']}; "
                       f"{fmt_money(float(g['fy26_ytd'].sum()))} Jan–Nov revenue rides on one seller.",
                "at_stake": float(g["fy26_ytd"].sum()), "accounts": int(len(g)), "capacity_high": hi,
                "entity_id": t["owner_id"], "ask": f"Show {owner}'s book"})

    # 2. purity breaks with a same-area fix
    rep = purity_report(data, a, territories)
    by_terr: dict[str, list[dict[str, Any]]] = {}
    for m in rep["misfits"]:
        by_terr.setdefault(m["territory_id"], []).append(m)
    for tid, ms in by_terr.items():
        fixes = [f for f in (purity_fix(data, accts, m, rules) for m in ms) if f and "error" not in f]
        if not fixes:
            continue
        t = tinfo.loc[tid]
        rev = float(sum(m["fy26_ytd"] for m in ms))
        cands.append({
            "kind": "purity", "title": f"Clean up {t['territory_name']}",
            "why": f"{len(ms)} {'account' if len(ms) == 1 else 'accounts'} of another sales motion ({fmt_money(rev)} Jan–Nov) "
                   f"in {a_an(t['motion'])} territory; "
                   f"first fix: {fixes[0]['account_name']} → {fixes[0]['to_territory_name']} ({fixes[0]['verdict']}).",
            "at_stake": rev, "misfits": len(ms), "entity_id": tid, "ask": f"Is {t['territory_name']} pure?"})

    # 3. fast growers in lighter-coverage motions
    tm = a["territory_id"].map(tinfo["motion"])
    grow = a[(a["yoy_pct"] >= rules.growing_yoy_pct) & (a["open_pipeline"] >= rules.large_pipeline_usd)
             & (tm.map(COVERAGE_LEVEL) < 3)]
    for r in grow.sort_values("open_pipeline", ascending=False).head(2).itertuples():
        cands.append({
            "kind": "cover_up", "title": f"Step up coverage on {r.account_name}",
            "why": f"{fmt_pct(r.yoy_pct)} YoY with {fmt_money(r.open_pipeline)} open pipeline, but covered as "
                   f"{tinfo.loc[r.territory_id, 'motion']} ({tinfo.loc[r.territory_id, 'territory_name']}).",
            "at_stake": float(r.open_pipeline), "yoy_pct": float(r.yoy_pct), "entity_id": r.account_id,
            "ask": f"Where should {r.account_name} go?"})

    # 4. sharp decliners that still carry big pipeline
    dec = a[(a["yoy_pct"] <= rules.declining_yoy_pct) & (a["open_pipeline"] >= rules.large_pipeline_usd)]
    for r in dec.sort_values("open_pipeline", ascending=False).head(2).itertuples():
        cands.append({
            "kind": "protect", "title": f"Protect {r.account_name}",
            "why": f"{fmt_pct(r.yoy_pct)} YoY ({fmt_money(r.fy26_ytd)} Jan–Nov) yet {fmt_money(r.open_pipeline)} open "
                   f"pipeline with {r.owner_name}. Review the plan before changing its owner.",
            "at_stake": float(r.open_pipeline), "yoy_pct": float(r.yoy_pct), "fy26_ytd": float(r.fy26_ytd),
            "entity_id": r.account_id, "ask": f"What's the pipeline for {r.account_name}?"})

    # rank: best of each kind first (variety), then by $ at stake
    cands.sort(key=lambda c: -c["at_stake"])
    seen, top, rest = set(), [], []
    for c in cands:
        (top if c["kind"] not in seen else rest).append(c)
        seen.add(c["kind"])
    ranked = (sorted(top, key=lambda c: -c["at_stake"]) + rest)[: max(1, limit)]
    for i, c in enumerate(ranked, 1):
        c["rank"] = i
        c["kind_label"] = KIND_LABEL[c["kind"]]
    return {"actions": ranked, "candidates": len(cands),
            "note": "Ranked by revenue or pipeline at stake; illustrative rules — validate with business owners."}
