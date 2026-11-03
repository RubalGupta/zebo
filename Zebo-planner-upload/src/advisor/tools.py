"""Allowlisted, scope-enforced planning tools used by every Advisor engine.

All tools are read-only. `evaluate_move` returns advice that the UI can turn into a draft-plan change only after a
human confirms it with a reason. Tool calls are bounded per turn and logged (name, safe args, status, time).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable

import pandas as pd

from planning.advice import evaluate_move, purity_fix, purity_report, suggest_targets
from planning.analysis import account_detail, entity_in_scope, entity_level, summarize
from planning.config import DEFAULT_RULES, PlanningRules
from planning.data import PlanningData
from planning.impact import moves_impact
from planning.plan_store import PlanStore, effective_accounts
from planning.scope import ID_COL, Persona, filter_scope

MAX_TOOL_CALLS_PER_TURN = 5
ID_RE = re.compile(r"^[A-Z0-9-]{2,16}$")
METRICS = {"fy26_ytd", "yoy_pct", "open_pipeline", "weighted_pipeline", "fy25_total", "declining_accounts", "accounts"}


class ToolError(Exception):
    pass


class PlanningToolkit:
    def __init__(self, data: PlanningData, store: PlanStore | None, persona: Persona,
                 rules: PlanningRules = DEFAULT_RULES, max_calls: int = MAX_TOOL_CALLS_PER_TURN):
        self.data, self.store, self.persona, self.rules = data, store, persona, rules
        self.max_calls = max_calls
        self.calls: list[dict[str, Any]] = []
        self.accts = effective_accounts(data, store)
        self.scoped = filter_scope(persona, self.accts)
        self.scoped_territories = filter_scope(persona, data.territories)

    # ---------------------------------------------------------------- registry
    SPECS: dict[str, dict[str, Any]] = {
        "scope_overview": {"description": "Summary of everything the current user can see, with children ranked.",
                           "params": {}},
        "drill": {"description": "Summary of one division, area, territory or account in scope, with its children.",
                  "params": {"entity_id": "id"}},
        "rank": {"description": "Rank children of an entity (default: the user's scope) by a metric.",
                 "params": {"level": "enum:division|area|territory|account", "metric": f"enum:{'|'.join(sorted(METRICS))}",
                            "order": "enum:desc|asc", "within_id": "id?", "limit": "int?"}},
        "account_detail": {"description": "Revenue, pipeline, signals and owner for one account in scope.",
                           "params": {"account_id": "id"}},
        "seller_book": {"description": "Accounts owned by one seller (AE) in scope, with territory load vs capacity and motion misfits.",
                        "params": {"seller_id": "id"}},
        "decline_drivers": {"description": "Who leads a division/area/territory and which sellers and accounts drive its revenue decline (Jan–Nov $ change).",
                            "params": {"within_id": "id", "limit": "int?"}},
        "pipeline_summary": {"description": "Open pipeline for a seller's book, an account, a territory, an area or a division: totals, by stage, near-term late-stage, top opportunities.",
                             "params": {"entity_id": "id", "limit": "int?"}},
        "evaluate_move": {"description": "Advice (verdict + reasons) for moving an account to another territory in scope. Does not change anything.",
                          "params": {"account_id": "id", "to_territory_id": "id"}},
        "suggest_targets": {"description": "Best-fit territories in scope for an account.", "params": {"account_id": "id"}},
        "purity_report": {"description": "Sales-motion purity of territories in scope (or within an entity).",
                          "params": {"within_id": "id?"}},
        "purity_fixes": {"description": "Suggested moves that fix the biggest purity breaks (optionally within a division, area or territory).",
                         "params": {"limit": "int?", "within_id": "id?"}},
        "plan_changes": {"description": "Moves in the draft FY27 plan.", "params": {}},
        "revenue_impact": {"description": "Estimated FY27 revenue impact (territory shifts + company effect, low/likely/high) of moving accounts to territories. Pairs by position.",
                           "params": {"account_ids": "idlist", "to_territory_ids": "idlist"}},
        "plan_revenue_impact": {"description": "Estimated FY27 revenue impact of all moves in the draft plan within scope.", "params": {}},
    }

    def call(self, name: str, **args) -> dict[str, Any]:
        status = "ok"
        try:
            if len(self.calls) >= self.max_calls:
                status = "rejected_limit"
                return {"error": "Tool-call limit reached", "detail": f"Max {self.max_calls} per turn."}
            if name not in self.SPECS:
                status = "rejected_not_allowlisted"
                return {"error": "Tool not allowlisted", "tool_name": str(name)[:40]}
            clean = self._validate(name, args)
            out = getattr(self, f"_t_{name}")(**clean)
            if isinstance(out, dict) and "error" in out:
                status = "error"
            return out
        except ToolError as exc:
            status = "error"
            return {"error": str(exc)}
        finally:
            self.calls.append({"tool": str(name)[:40], "args": {k: (v if isinstance(v, (int, float)) else str(v)[:40])
                                                               for k, v in list(args.items())[:6]},
                               "status": status, "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat()})

    def _validate(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        spec = self.SPECS[name]["params"]
        extra = set(args) - set(spec)
        if extra:
            raise ToolError(f"Invalid tool arguments: unexpected {', '.join(sorted(extra))}")
        clean: dict[str, Any] = {}
        for p, kind in spec.items():
            optional = kind.endswith("?")
            kind = kind.rstrip("?")
            v = args.get(p)
            if v in (None, ""):
                if not optional:
                    raise ToolError(f"Invalid tool arguments: missing {p}")
                continue
            if kind == "id":
                v = str(v).strip().upper()
                if not ID_RE.match(v):
                    raise ToolError(f"Invalid tool arguments: {p} has an invalid format")
            elif kind == "idlist":
                items = v if isinstance(v, (list, tuple)) else str(v).split(",")
                items = [str(x).strip().upper() for x in items if str(x).strip()]
                if not items or len(items) > 10 or not all(ID_RE.match(x) for x in items):
                    raise ToolError(f"Invalid tool arguments: {p} must be 1-10 IDs")
                v = items
            elif kind == "int":
                try:
                    v = max(1, min(50, int(v)))
                except (TypeError, ValueError):
                    raise ToolError(f"Invalid tool arguments: {p} must be an integer")
            elif kind.startswith("enum:"):
                if str(v) not in kind[5:].split("|"):
                    raise ToolError(f"Invalid tool arguments: {p} must be one of {kind[5:]}")
            clean[p] = v
        return clean

    def _scope_check(self, level: str | None, eid: str) -> None:
        if level is None:
            raise ToolError(f"Unknown id: {eid}")
        if level == "seller":
            return
        if not entity_in_scope(self.data, self.accts, self.persona, level, eid):
            raise ToolError(f"{self.data.name_of(eid)} is outside your access ({self.persona.label}).")

    # ---------------------------------------------------------------- tools
    def _t_scope_overview(self) -> dict[str, Any]:
        out = summarize(self.data, self.accts, self.persona.level, self.persona.scope_id)
        out["purity"] = {k: v for k, v in purity_report(self.data, self.scoped, self.scoped_territories).items()
                         if k in ("pure", "mixed", "critical", "empty")}
        out["plan_moves"] = len(self.store.moves()) if self.store else 0
        out["declining_threshold_pct"] = self.rules.declining_yoy_pct
        return out

    def _t_drill(self, entity_id: str) -> dict[str, Any]:
        lvl = entity_level(self.data, entity_id)
        self._scope_check(lvl, entity_id)
        if lvl == "account":
            return account_detail(self.data, self.accts, entity_id)
        return summarize(self.data, self.accts, lvl, entity_id)

    def _t_rank(self, level: str, metric: str, order: str = "desc", within_id: str | None = None,
                limit: int = 5) -> dict[str, Any]:
        base_level, base_id = self.persona.level, self.persona.scope_id
        if within_id:
            lvl = entity_level(self.data, within_id)
            self._scope_check(lvl, within_id)
            base_level, base_id = lvl, within_id
        sub = self.accts if base_level == "bu" else self.accts[self.accts[ID_COL[base_level]] == base_id]
        if level == "account":
            df = sub.copy()
            if metric in ("accounts", "declining_accounts"):
                raise ToolError("That metric applies to groups, not single accounts")
            df = df.dropna(subset=[metric]) if metric == "yoy_pct" else df
            df = df.sort_values(metric, ascending=(order == "asc")).head(limit or 5)
            rows = [{"id": r.account_id, "name": r.account_name, "motion": r.motion, "owner": r.owner_name,
                     "fy26_ytd": r.fy26_ytd, "yoy_pct": None if pd.isna(r.yoy_pct) else r.yoy_pct,
                     "open_pipeline": r.open_pipeline} for r in df.itertuples()]
        else:
            order_levels = ["bu", "division", "area", "territory"]
            if order_levels.index(level) <= order_levels.index(base_level):
                raise ToolError(f"Can't rank {level}s inside a {base_level}")
            groups = []
            for gid, g in sub.groupby(ID_COL[level]):
                f25, f26 = g["fy25_ytd"].sum(), g["fy26_ytd"].sum()
                groups.append({"id": gid, "name": self.data.name_of(gid), "accounts": len(g), "fy26_ytd": f26,
                               "yoy_pct": (f26 / f25 - 1) * 100 if f25 > 0 else None,
                               "open_pipeline": g["open_pipeline"].sum(), "weighted_pipeline": g["weighted_pipeline"].sum(),
                               "fy25_total": g["fy25_total"].sum(), "declining_accounts": int((g["yoy_pct"] <= -15).sum())})
            groups = [x for x in groups if x.get(metric) is not None]
            groups.sort(key=lambda x: x[metric], reverse=(order == "desc"))
            rows = groups[:limit or 5]
        return {"level": level, "metric": metric, "order": order, "within": self.data.name_of(base_id),
                "within_id": base_id, "within_level": base_level, "rows": rows, "ytd_months": self.data.ytd_months}

    def _t_account_detail(self, account_id: str) -> dict[str, Any]:
        self._scope_check(entity_level(self.data, account_id), account_id)
        return account_detail(self.data, self.accts, account_id)

    def _t_seller_book(self, seller_id: str) -> dict[str, Any]:
        s = self.data.sellers[self.data.sellers["seller_id"] == seller_id]
        if s.empty:
            raise ToolError(f"Unknown seller: {seller_id}")
        book = self.scoped[self.scoped["owner_id"] == seller_id]
        terr = self.data.territories[self.data.territories["owner_id"] == seller_id]
        if book.empty and (terr.empty or not entity_in_scope(self.data, self.accts, self.persona, "territory",
                                                               terr.iloc[0]["territory_id"])):
            raise ToolError(f"{s.iloc[0]['seller_name']} has no accounts within your access.")
        tmotion = None if terr.empty else terr.iloc[0]["motion"]
        lo, hi = self.rules.capacity(tmotion) if tmotion else (None, None)
        misfits = book[book["motion"] != tmotion] if tmotion else book.iloc[0:0]
        f25 = float(book["fy25_ytd"].sum())
        return {"seller_id": seller_id, "seller_name": s.iloc[0]["seller_name"], "role": s.iloc[0]["role"],
                "territory_id": None if terr.empty else terr.iloc[0]["territory_id"],
                "territory_name": None if terr.empty else terr.iloc[0]["territory_name"],
                "territory_motion": tmotion, "capacity_low": lo, "capacity_high": hi,
                "over_capacity": bool(hi is not None and len(book) > hi),
                "misfits": [{"id": r.account_id, "name": r.account_name, "motion": r.motion} for r in misfits.itertuples()],
                "fy25_ytd": f25, "yoy_pct": (float(book["fy26_ytd"].sum()) / f25 - 1) * 100 if f25 > 0 else None,
                "weighted_pipeline": float(book["weighted_pipeline"].sum()),
                "accounts": [{"id": r.account_id, "name": r.account_name, "motion": r.motion, "fy26_ytd": r.fy26_ytd,
                              "yoy_pct": None if pd.isna(r.yoy_pct) else r.yoy_pct, "open_pipeline": r.open_pipeline}
                             for r in book.sort_values("fy26_ytd", ascending=False).itertuples()],
                "fy26_ytd": float(book["fy26_ytd"].sum()), "open_pipeline": float(book["open_pipeline"].sum())}

    def _t_evaluate_move(self, account_id: str, to_territory_id: str) -> dict[str, Any]:
        self._scope_check(entity_level(self.data, account_id), account_id)
        self._scope_check(entity_level(self.data, to_territory_id), to_territory_id)
        return evaluate_move(self.data, self.accts, account_id, to_territory_id, self.rules)

    def _t_suggest_targets(self, account_id: str) -> dict[str, Any]:
        self._scope_check(entity_level(self.data, account_id), account_id)
        opts = suggest_targets(self.data, self.accts, account_id, list(self.scoped_territories["territory_id"]), self.rules)
        return {"account_id": account_id, "account_name": self.data.name_of(account_id), "options": opts}

    def _t_purity_report(self, within_id: str | None = None) -> dict[str, Any]:
        accts, terrs = self.scoped, self.scoped_territories
        if within_id:
            lvl = entity_level(self.data, within_id)
            self._scope_check(lvl, within_id)
            if lvl not in ("division", "area", "territory"):
                raise ToolError("Purity applies to a division, area or territory")
            accts = accts[accts[ID_COL[lvl]] == within_id]
            terrs = terrs[terrs[ID_COL[lvl]] == within_id]
        rep = purity_report(self.data, accts, terrs)
        rep["within"] = self.data.name_of(within_id or self.persona.scope_id)
        return rep

    def _t_purity_fixes(self, limit: int = 3, within_id: str | None = None) -> dict[str, Any]:
        accts, terrs = self.scoped, self.scoped_territories
        if within_id:
            lvl = entity_level(self.data, within_id)
            self._scope_check(lvl, within_id)
            if lvl not in ("division", "area", "territory"):
                raise ToolError("Purity applies to a division, area or territory")
            accts = accts[accts[ID_COL[lvl]] == within_id]
            terrs = terrs[terrs[ID_COL[lvl]] == within_id]
        rep = purity_report(self.data, accts, terrs)
        fixes = []
        for m in rep["misfits"]:
            f = purity_fix(self.data, self.accts, m, self.rules)
            if f and "error" not in f:
                fixes.append(f)
            if len(fixes) >= (limit or 3):
                break
        return {"fixes": fixes, "misfit_total": len(rep["misfits"])}

    def _t_decline_drivers(self, within_id: str, limit: int = 3) -> dict[str, Any]:
        lvl = entity_level(self.data, within_id)
        self._scope_check(lvl, within_id)
        if lvl not in ("division", "area", "territory"):
            raise ToolError("Decline drivers apply to a division, area or territory")
        g = self.accts[self.accts[ID_COL[lvl]] == within_id].copy()
        g["change"] = g["fy26_ytd"] - g["fy25_ytd"]
        f25, f26 = float(g["fy25_ytd"].sum()), float(g["fy26_ytd"].sum())
        total = f26 - f25
        if lvl == "territory":
            leader_id = self.data.territories.loc[self.data.territories["territory_id"] == within_id, "owner_id"].iloc[0]
        else:
            leader_id = self.data.hierarchy.loc[self.data.hierarchy["id"] == within_id, "leader_id"].iloc[0]
        leader = self.data.sellers[self.data.sellers["seller_id"] == leader_id]
        decliners = g[g["change"] < 0]
        lost = float(decliners["change"].sum())
        sellers = []
        for sid, sg in decliners.groupby("owner_id"):
            terr = self.data.territories[self.data.territories["owner_id"] == sid]
            sellers.append({"seller_id": sid, "seller_name": self.data.name_of(sid),
                            "territory_name": None if terr.empty else terr.iloc[0]["territory_name"],
                            "change": float(sg["change"].sum()), "declining_accounts": int(len(sg)),
                            "share_of_decline_pct": float(sg["change"].sum() / lost * 100) if lost < 0 else None})
        sellers.sort(key=lambda x: x["change"])
        top_accts = [{"id": r.account_id, "name": r.account_name, "owner": r.owner_name, "change": float(r.change),
                      "yoy_pct": None if pd.isna(r.yoy_pct) else float(r.yoy_pct)}
                     for r in decliners.sort_values("change").head(limit or 3).itertuples()]
        return {"within_id": within_id, "within": self.data.name_of(within_id), "level": lvl,
                "leader_id": leader_id, "leader": self.data.name_of(leader_id),
                "leader_role": None if leader.empty else leader.iloc[0]["role"],
                "fy25_ytd": f25, "fy26_ytd": f26, "change": total, "yoy_pct": (f26 / f25 - 1) * 100 if f25 > 0 else None,
                "lost_in_declining_accounts": lost, "sellers": sellers[:limit or 3], "accounts": top_accts}

    def _t_pipeline_summary(self, entity_id: str, limit: int = 5) -> dict[str, Any]:
        is_seller = entity_id in set(self.data.sellers["seller_id"])
        lvl = "seller" if is_seller else entity_level(self.data, entity_id)
        self._scope_check(lvl, entity_id)
        if lvl == "seller":
            ids = self.scoped.loc[self.scoped["owner_id"] == entity_id, "account_id"]
            if ids.empty:
                raise ToolError(f"{self.data.name_of(entity_id)} has no accounts within your access.")
        elif lvl == "account":
            ids = pd.Series([entity_id])
        elif lvl == "bu":
            ids = self.scoped["account_id"]
        else:
            ids = self.accts.loc[self.accts[ID_COL[lvl]] == entity_id, "account_id"]
        p = self.data.pipeline[self.data.pipeline["account_id"].isin(set(ids))]
        names = dict(zip(self.accts["account_id"], self.accts["account_name"]))
        late = p[(p["probability"] >= self.rules.late_stage_probability) & p["close_period"].isin(self.rules.near_term_periods)]
        stages = [{"stage": st_, "count": int(len(sg)), "amount": float(sg["amount_usd"].sum())}
                  for st_, sg in p.groupby("stage")]
        stages.sort(key=lambda x: -x["amount"])
        top = [{"opportunity_id": r.opportunity_id, "account_id": r.account_id, "account_name": names.get(r.account_id, r.account_id),
                "stage": r.stage, "probability_pct": float(r.probability * 100), "amount": float(r.amount_usd),
                "close_period": r.close_period}
               for r in p.sort_values("amount_usd", ascending=False).head(limit or 5).itertuples()]
        return {"entity_id": entity_id, "entity": self.data.name_of(entity_id), "level": lvl, "accounts": int(len(set(ids))),
                "opportunities": int(len(p)), "open_pipeline": float(p["amount_usd"].sum()),
                "weighted_pipeline": float(p["weighted_usd"].sum()), "near_term_late_stage": float(late["amount_usd"].sum()),
                "near_term_periods": list(self.rules.near_term_periods), "by_stage": stages, "top": top}

    def _t_plan_changes(self) -> dict[str, Any]:
        moves = self.store.moves() if self.store else []
        mine = [m for m in moves if m["account_id"] in set(self.scoped["account_id"])]
        return {"moves": mine, "count": len(mine)}

    def _t_revenue_impact(self, account_ids: list[str], to_territory_ids: list[str]) -> dict[str, Any]:
        if len(account_ids) != len(to_territory_ids):
            raise ToolError("Invalid tool arguments: account_ids and to_territory_ids must have the same length")
        for a, t in zip(account_ids, to_territory_ids):
            self._scope_check(entity_level(self.data, a), a)
            self._scope_check(entity_level(self.data, t), t)
        return moves_impact(self.data, self.accts, list(zip(account_ids, to_territory_ids)), self.rules)

    def _t_plan_revenue_impact(self) -> dict[str, Any]:
        """Impact of the draft plan's moves vs the FY26 book: evaluate each moved account from its FY26 territory."""
        base = effective_accounts(self.data, None)
        moved = self.accts[self.accts["moved"] & self.accts["account_id"].isin(self.scoped["account_id"])]
        pairs = list(zip(moved["account_id"], moved["territory_id"]))
        out = moves_impact(self.data, base, pairs, self.rules) if pairs else {"items": [], "territory_shifts": [],
                                                                             "company_delta": 0.0, "errors": []}
        out["move_count"] = len(pairs)
        return out

    # ---------------------------------------------------------------- for model engines
    def json_schemas(self) -> list[dict[str, Any]]:
        out = []
        for name, spec in self.SPECS.items():
            props, req = {}, []
            for p, kind in spec["params"].items():
                opt = kind.endswith("?")
                k = kind.rstrip("?")
                if k == "int":
                    props[p] = {"type": "integer"}
                elif k == "idlist":
                    props[p] = {"type": "array", "items": {"type": "string"}, "maxItems": 10}
                elif k.startswith("enum:"):
                    props[p] = {"type": "string", "enum": k[5:].split("|")}
                else:
                    props[p] = {"type": "string", "description": "An ID such as NAW, NAW-A02, NAW-A02-D1, A0091 or S0012"}
                if not opt:
                    req.append(p)
            out.append({"name": name, "description": spec["description"],
                        "input_schema": {"type": "object", "properties": props, "required": req}})
        return out
