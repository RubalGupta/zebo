"""Rollups, drill-down and entity lookup over the (plan-adjusted, scope-filtered) book of business."""
from __future__ import annotations

import difflib
import re
from typing import Any

import pandas as pd

from planning.data import PlanningData
from planning.scope import CHILD, ID_COL, Persona, filter_scope


def fmt_money(v: float | None) -> str:
    if v is None or pd.isna(v):
        return "n/a"
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1e9:
        return f"{sign}${a / 1e9:.2f}B"
    if a >= 1e6:
        return f"{sign}${a / 1e6:.1f}M"
    if a >= 1e3:
        return f"{sign}${a / 1e3:.0f}K"
    return f"{sign}${a:.0f}"


def a_an(word: str) -> str:
    """'a Deep' / 'an Expansion'."""
    return ("an " if str(word)[:1].lower() in "aeiou" else "a ") + str(word)


def fmt_pct(v: float | None) -> str:
    return "n/a" if v is None or pd.isna(v) else f"{v:+.1f}%"


def _metrics(g: pd.DataFrame) -> dict[str, Any]:
    fy25 = g["fy25_ytd"].sum()
    fy26 = g["fy26_ytd"].sum()
    return {
        "accounts": int(len(g)),
        "fy25_total": float(g["fy25_total"].sum()),
        "fy25_ytd": float(fy25),
        "fy26_ytd": float(fy26),
        "yoy_pct": float((fy26 / fy25 - 1) * 100) if fy25 > 0 else None,
        "open_pipeline": float(g["open_pipeline"].sum()),
        "weighted_pipeline": float(g["weighted_pipeline"].sum()),
        "declining_accounts": int((g["yoy_pct"] <= -15).sum()),
    }


def entity_level(data: PlanningData, entity_id: str) -> str | None:
    h = data.hierarchy[data.hierarchy["id"] == entity_id]
    if len(h):
        return str(h.iloc[0]["level"])
    if entity_id in set(data.territories["territory_id"]):
        return "territory"
    if entity_id in set(data.accounts["account_id"]):
        return "account"
    return None


def scope_root(persona: Persona) -> tuple[str, str]:
    return persona.level, persona.scope_id


def entity_in_scope(data: PlanningData, accts: pd.DataFrame, persona: Persona, level: str, entity_id: str) -> bool:
    """True when the entity sits inside the persona's scope (based on the plan-adjusted book)."""
    if persona.level == "bu":
        return True
    if level == persona.level:
        return entity_id == persona.scope_id
    if level in ("bu",) or (persona.level == "area" and level == "division"):
        return False
    if level == "territory":
        t = data.territories[data.territories["territory_id"] == entity_id]
        return bool(len(t)) and str(t.iloc[0][ID_COL[persona.level]]) == persona.scope_id
    if level == "area":
        h = data.hierarchy[data.hierarchy["id"] == entity_id]
        return bool(len(h)) and persona.level == "division" and h.iloc[0]["parent_id"] == persona.scope_id
    if level == "account":
        a = filter_scope(persona, accts)
        return entity_id in set(a["account_id"])
    return False


def summarize(data: PlanningData, accts: pd.DataFrame, level: str, entity_id: str) -> dict[str, Any]:
    """Metrics for one entity plus its children ranked by FY26 YTD revenue."""
    sub = accts if level == "bu" else accts[accts[ID_COL[level]] == entity_id]
    if level == "account":
        return account_detail(data, accts, entity_id)
    out = {"level": level, "id": entity_id, "name": data.name_of(entity_id), **_metrics(sub)}
    terrs = data.territories if level == "bu" else data.territories[data.territories[ID_COL[level]] == entity_id]
    out["territories"] = int(len(terrs))
    out["ytd_months"] = data.ytd_months
    child = CHILD[level]
    if child == "account":
        rows = sub.sort_values("fy26_ytd", ascending=False)
        out["children"] = [{"level": "account", "id": r.account_id, "name": r.account_name, "motion": r.motion,
                            "owner": r.owner_name, "fy26_ytd": r.fy26_ytd, "yoy_pct": None if pd.isna(r.yoy_pct) else r.yoy_pct,
                            "open_pipeline": r.open_pipeline, "moved": bool(r.moved) if "moved" in sub else False}
                           for r in rows.itertuples()]
        t = data.territories[data.territories["territory_id"] == entity_id].iloc[0]
        out["motion"] = t["motion"]
        out["owner"] = data.name_of(t["owner_id"])
    else:
        kids = []
        child_ids = (data.hierarchy[(data.hierarchy["level"] == child) & (data.hierarchy["parent_id"] == entity_id)]["id"]
                     if child in ("division", "area") else terrs["territory_id"])
        for cid in child_ids:
            g = sub[sub[ID_COL[child]] == cid]
            row = {"level": child, "id": cid, "name": data.name_of(cid), **_metrics(g)}
            if child == "territory":
                t = data.territories[data.territories["territory_id"] == cid].iloc[0]
                row["motion"], row["owner"] = t["motion"], data.name_of(t["owner_id"])
            kids.append(row)
        out["children"] = sorted(kids, key=lambda r: r["fy26_ytd"], reverse=True)
    return out


def account_detail(data: PlanningData, accts: pd.DataFrame, account_id: str) -> dict[str, Any]:
    a = accts[accts["account_id"] == account_id]
    if a.empty:
        return {"error": f"Unknown account: {account_id}"}
    r = a.iloc[0]
    t = data.territories[data.territories["territory_id"] == r["territory_id"]].iloc[0]
    opps = data.pipeline[data.pipeline["account_id"] == account_id].sort_values("amount_usd", ascending=False)
    monthly = data.revenue[data.revenue["account_id"] == account_id]
    return {
        "level": "account", "id": account_id, "name": r["account_name"], "industry": r["industry"],
        "employee_band": r["employee_band"], "motion": r["motion"], "territory_id": r["territory_id"],
        "territory_name": t["territory_name"], "territory_motion": t["motion"], "owner": r["owner_name"],
        "area": data.name_of(r["area_id"]), "division": data.name_of(r["division_id"]),
        "fy25_total": float(r["fy25_total"]), "fy25_ytd": float(r["fy25_ytd"]), "fy26_ytd": float(r["fy26_ytd"]),
        "yoy_pct": None if pd.isna(r["yoy_pct"]) else float(r["yoy_pct"]),
        "open_pipeline": float(r["open_pipeline"]), "weighted_pipeline": float(r["weighted_pipeline"]),
        "opportunities": opps[["opportunity_id", "stage", "probability", "amount_usd", "close_period"]].to_dict("records"),
        "signal": r.get("signal", ""), "signal_impact": r.get("impact", ""),
        "intent_score": None if pd.isna(r.get("intent_score")) else r.get("intent_score"),
        "industry_outlook_pct": None if pd.isna(r.get("fy27_outlook_pct")) else float(r["fy27_outlook_pct"]),
        "monthly": monthly[["fiscal_year", "month", "revenue_usd"]].to_dict("records"),
        "moved_in_plan": bool(r.get("moved", False)), "fy26_territory_id": r.get("fy26_territory_id", r["territory_id"]),
        "ytd_months": data.ytd_months,
    }


# ------------------------------------------------------------------ entity resolution (for chat)

_STOP = {"the", "show", "me", "into", "drill", "open", "zoom", "go", "to", "for", "of", "in", "area", "division",
         "territory", "account", "details", "detail", "about", "tell", "what", "is", "and", "please", "my", "on",
         "move", "from", "a", "an", "how", "doing", "summary", "who", "owns", "should", "where", "go?"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 &-]", " ", s.lower()).strip()


def candidates(data: PlanningData) -> list[tuple[str, str, str]]:
    """(display name, level, id) for every resolvable entity."""
    c = [(r["name"], r["level"], r["id"]) for _, r in data.hierarchy.iterrows()]
    c += [(r.territory_name, "territory", r.territory_id) for r in data.territories.itertuples()]
    c += [(r.account_name, "account", r.account_id) for r in data.accounts.itertuples()]
    c += [(r.seller_name, "seller", r.seller_id) for r in data.sellers.itertuples()]
    return c


def resolve_entities(data: PlanningData, text: str, levels: tuple[str, ...] | None = None,
                     limit: int = 3) -> list[dict[str, str]]:
    """Find entities mentioned in free text: exact IDs first, then names (longest match wins), then fuzzy."""
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    t = text.strip()
    for tok in re.findall(r"\b(?:[A-Z]{2,4}-A\d{2}(?:-[DEG]\d)?|A\d{4}|S\d{4}|[A-Z]{2,4}|BU1)\b", t.upper()):
        lvl = entity_level(data, tok)
        if lvl is None and tok in set(data.sellers["seller_id"]):
            lvl = "seller"
        if lvl and (levels is None or lvl in levels) and tok not in seen:
            found.append({"level": lvl, "id": tok, "name": data.name_of(tok)})
            seen.add(tok)
    nt = " " + _norm(t) + " "
    cands = [c for c in candidates(data) if levels is None or c[1] in levels]
    hits = []
    for name, lvl, eid in cands:
        n = _norm(name)
        if len(n) >= 4 and f" {n} " in nt:
            hits.append((len(n), name, lvl, eid))
    for _, name, lvl, eid in sorted(hits, reverse=True):
        if eid not in seen and not any(name.lower() in f["name"].lower() for f in found):
            found.append({"level": lvl, "id": eid, "name": name})
            seen.add(eid)
    if not found:
        words = [w for w in _norm(t).split() if w not in _STOP and len(w) > 2]
        phrase = " ".join(words)
        if phrase:
            names = {c[0].lower(): c for c in cands}
            for m in difflib.get_close_matches(phrase, list(names), n=limit, cutoff=0.72):
                name, lvl, eid = names[m]
                found.append({"level": lvl, "id": eid, "name": name, "fuzzy": "true"})
    return found[:limit]
