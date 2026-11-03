"""RuleAdvisor — the free, no-model Advisor engine with conversational drill-down context.

How it works (transparent by design):
1. Code-level guardrails first (individual-performance / employment requests, code/system-write requests).
2. A keyword intent + entity resolution (IDs, names, fuzzy names) within the user's access scope.
3. One to five allowlisted tool calls; the answer is composed only from tool output.
4. Every $ / % in the answer is checked against tool output (grounding).
It remembers the current focus (e.g. "Pacific Northwest") so follow-ups like "which accounts are declining?"
apply there. Moves are returned as proposals; a human applies them.
"""
from __future__ import annotations

import re
from typing import Any

from advisor import grounding
from advisor.tools import PlanningToolkit
from planning.analysis import a_an, fmt_money, fmt_pct, resolve_entities
from planning.impact import summary_lines
from planning.scope import CHILD

ENGINE_NAME = "Rule-based Advisor (no AI model)"

_SENSITIVE = re.compile(r"\b(fire|fired|firing|terminat\w*|lay\s?off\w*|let\s+go|dismiss\w*|disciplin\w*|"
                        r"performance improvement plan|demot\w*)\b", re.I)
_RANK_PEOPLE = re.compile(r"\b(rank|worst|bottom|underperform\w*|low\s+performer\w*|weakest)\b.*\b(reps?|sellers?|"
                          r"people|employees?|salespeople|aes?)\b", re.I)
_BLOCK = re.compile(r"\b(execute|run)\b.*\b(code|script|shell|sql|python)\b|\b(crm|salesforce)\b.*\b(update|write|push|change)\b"
                    r"|\b(update|write|push|change)\b.*\b(crm|salesforce)\b|drop\s+table|import\s+os", re.I)
_LATER = {
    2: re.compile(r"\b(comp(ensation)?\s*plan|incentive|ote|commission|non-incentive|hierarch\w*|roles?\b|"
                  r"december|dec\s+revenue|revenue\s+lock|lock\s+revenue|true-?up|extrapolat\w*|verify\s+revenue|"
                  r"discrepanc\w*)\b", re.I),
    3: re.compile(r"\b(quota\w*|allocat\w*|dispute|contest|seller\s+view|my\s+quota|payout|attainment)\b", re.I),
}
_HELP = re.compile(r"\b(help|what can you do|how do i|capabilit\w*)\b", re.I)
_BRIEF = re.compile(r"^\s*(hi|hello|hey|good (morning|afternoon|evening))\b|\b(brief|overview|big picture|my business|"
                    r"where do i start|summar\w+ (my|the) (business|book|scope)|how('s| is| are) (my|the) business)\b", re.I)
_UNDO = re.compile(r"\b(undo|revert|roll\s*back)\b", re.I)
_CHANGES = re.compile(r"\b(my changes|plan changes|what did i (change|move)|draft plan|moves so far|show (the )?changes)\b", re.I)
_PURITY = re.compile(r"\b(pur(e|ity)|mixed motion|misfit\w*|misalign\w*|wrong territor\w*|motion mismatch)\b", re.I)
_IMPACT_STRONG = re.compile(r"\b(impact\w*|affect\w*|effect\w*)\b", re.I)
_REVENUE = re.compile(r"\b(revenue|fy\s?27|2027|next year|bookings|number|forecast|top\s?line)\b", re.I)
_REF = re.compile(r"\b(if|change\w*|move\w*|these|this|them|those|that|it|plan|fix\w*|suggest\w*)\b", re.I)
_PLAN_WORDS = re.compile(r"\b(my plan|draft plan|the plan|my changes|applied|so far)\b", re.I)


def _sgn(v: float) -> str:
    return ("+" if v >= 0 else "") + fmt_money(v)


def _is_impact(msg: str) -> bool:
    return bool((_IMPACT_STRONG.search(msg) and (_REVENUE.search(msg) or _REF.search(msg)))
                or (_REVENUE.search(msg) and _REF.search(msg) and re.search(r"\b(how|what|will|would)\b", msg, re.I)))


_MOVE = re.compile(r"\b(move|reassign|transfer|shift|put|give)\b|\bwhat if\b", re.I)
_WHERE = re.compile(r"\b(where should|best (territory|owner|fit|home)|suggest (a |an )?(territory|owner|home)|who should own)\b", re.I)
_WHO_OWNS = re.compile(r"\b(who owns|owner of|who covers|who is the owner)\b", re.I)
_BOOK = re.compile(r"\b(book|accounts?) (of|for|owned by)\b|\b's (book|accounts)\b", re.I)
_UP = re.compile(r"\b(go up|back up|zoom out|up a level|go back|parent)\b", re.I)
_SHOULD = re.compile(r"\b(should|shall) (i|we)\b.*\b(implement\w*|apply|do (it|this|that|them)|make (it|this|that|the|these)|"
                     r"accept|approve|go ahead|go with|take (it|this|that|the))\b|\bdo you recommend (it|this|that|the)\b"
                     r"|\bis it (a good idea|worth it)\b", re.I)
_BOOK_REF = re.compile(r"\b(his|her|their|the seller'?s?|that seller'?s?|entire|whole|full)\b.*\bbook\b|\bbook\b", re.I)
_PIPE = re.compile(r"\bpipeline\b|\bopportunit\w*\b|\bopps?\b|\bdeals?\b", re.I)
_NEW_TERR = re.compile(r"\b(new|receiving|their|his|her|that)\b.*\bterritor(y|ies)\b|\bnew seller\b", re.I)
_RANK = re.compile(r"\b(top|biggest|largest|highest|lowest|smallest|most|least|declin\w*|grow\w*|fastest|slowest|"
                   r"worst|best|shrink\w*|drop\w*|which)\b", re.I)
_LEVEL_WORDS = [("account", r"\baccounts?\b|\bcustomers?\b|\blogos?\b"), ("territory", r"\bterritor(y|ies)\b"),
                ("area", r"\bareas?\b"), ("division", r"\bdivisions?\b")]

def _plural(level: str) -> str:
    return {"territory": "territories", "division": "divisions", "area": "areas", "account": "accounts"}.get(level, level + "s")


LATER_TEXT = {
    2: ("That's part of **Phase 2** (sales hierarchy & comp plans, current-year revenue verification and the "
        "December estimate). In Phase 2 I'll flag revenue discrepancies automatically, estimate December with a "
        "low/likely/high range, and help you lock the baseline with a January true-up rule."),
    3: ("That's part of **Phase 3** (quota model, BU → area → territory → account allocation, seller quota view, "
        "estimated payout and disputes). Phase 1 covers your business view, accounts, moves and territory purity."),
}


class RuleAdvisor:
    name = ENGINE_NAME

    def __init__(self, toolkit_factory):
        self._factory = toolkit_factory   # () -> PlanningToolkit (fresh per turn: bounded calls, current plan)

    # ---------------------------------------------------------------- public
    def respond(self, message: str, ctx: dict[str, Any]) -> dict[str, Any]:
        tk: PlanningToolkit = self._factory()
        msg = (message or "").strip()[:1500]
        ctx.setdefault("focus", None)
        reply = self._route(msg, ctx, tk)
        reply.setdefault("proposals", [])
        reply.setdefault("suggestions", [])
        reply.setdefault("focus", ctx.get("focus"))
        reply["tool_calls"] = tk.calls
        reply["engine"] = self.name
        reply["grounding"] = grounding.check(reply["text"], reply.pop("_results", []))
        ctx["focus"] = reply["focus"]
        if reply["proposals"]:   # remember what was just proposed so "what's the impact of these?" works
            ctx["last_proposals"] = [(p["account_id"], p["to_territory_id"]) for p in reply["proposals"]]
        return reply

    def briefing(self, ctx: dict[str, Any]) -> dict[str, Any]:
        return self.respond("brief me", ctx)

    # ---------------------------------------------------------------- routing
    def _route(self, msg: str, ctx: dict[str, Any], tk: PlanningToolkit) -> dict[str, Any]:
        if not msg:
            return {"text": "Ask me about your business — for example *“brief me”*, *“drill into Pacific Northwest”*, "
                            "*“which accounts are declining?”* or *“are my territories pure?”*."}
        if _SENSITIVE.search(msg) or _RANK_PEOPLE.search(msg):
            return {"text": "I can't rank individual sellers or support termination, discipline or individual performance "
                            "decisions. I work at the level of territories, accounts and coverage; people decisions need "
                            "your HR and legal process.", "suggestions": ["Brief me", "Are my territories pure?"]}
        if _BLOCK.search(msg):
            return {"text": "I can't run code or write to the CRM or any system. Plan changes happen only in the draft "
                            "FY27 plan, and only when you apply them."}
        for phase in (2, 3):
            if _LATER[phase].search(msg) and not _MOVE.search(msg):
                return {"text": LATER_TEXT[phase], "suggestions": ["Brief me", "Are my territories pure?"]}
        if _SHOULD.search(msg) and not _WHERE.search(msg) and not _PURITY.search(msg):
            return self._should(msg, ctx, tk)
        if _is_impact(msg):
            return self._impact(msg, ctx, tk)
        if _UNDO.search(msg):
            return self._undo_hint(tk)
        if _CHANGES.search(msg):
            return self._changes(tk)
        if _PURITY.search(msg) and not _MOVE.search(msg):
            return self._purity(msg, ctx, tk)
        if _WHERE.search(msg):
            return self._where(msg, tk)
        if _MOVE.search(msg):
            return self._move(msg, ctx, tk)
        if _WHO_OWNS.search(msg):
            return self._who_owns(msg, ctx, tk)
        if _BOOK.search(msg) or _BOOK_REF.search(msg):
            ents = resolve_entities(tk.data, msg, ("seller",))
            if ents:
                return self._book(ents[0], ctx, tk)
            subj = ctx.get("subject")
            if subj and subj[0] == "seller" and _BOOK_REF.search(msg):
                return self._book({"id": subj[1], "name": tk.data.name_of(subj[1]), "level": "seller"}, ctx, tk)
        if _PIPE.search(msg) and not (_RANK.search(msg) and any(re.search(p, msg, re.I) for _, p in _LEVEL_WORDS)):
            return self._pipeline(msg, ctx, tk)
        if _UP.search(msg):
            return self._up(ctx, tk)
        if _BRIEF.search(msg):
            return self._brief(ctx, tk)
        if _RANK.search(msg) and any(re.search(p, msg, re.I) for _, p in _LEVEL_WORDS):
            return self._rank(msg, ctx, tk)
        ents = resolve_entities(tk.data, msg, ("division", "area", "territory", "account", "seller", "bu"))
        if ents:
            e = ents[0]
            if e["level"] == "seller":
                return self._book(e, ctx, tk)
            return self._drill(e, ctx, tk)
        if _HELP.search(msg):
            return self._help()
        sugg = ["Brief me", "Which areas are declining?", "Are my territories pure?"]
        if ctx.get("last_proposals"):
            sugg = ["What's the FY27 revenue impact of these moves?"] + sugg[:2]
        return {"text": "I didn't catch an entity or a request I support, so I haven't looked anything up. "
                        "I can brief you, drill into a division/area/territory/account, rank accounts or areas, "
                        "evaluate a move, estimate the FY27 revenue impact of moves, and check territory purity. "
                        "I can't infer business context that isn't in the data.", "suggestions": sugg}

    # ---------------------------------------------------------------- handlers
    def _brief(self, ctx, tk):
        o = tk.call("scope_overview")
        if "error" in o:
            return {"text": o["error"], "_results": [o]}
        kids = [k for k in o["children"] if k.get("yoy_pct") is not None]
        up = max(kids, key=lambda k: k["yoy_pct"]) if kids else None
        down = min(kids, key=lambda k: k["yoy_pct"]) if kids else None
        p = o["purity"]
        child = CHILD[o["level"]]
        lines = [f"**You're viewing {o['name']}** — {o['accounts']} accounts across {o['territories']} territories.",
                 f"- FY26 revenue Jan–Nov: **{fmt_money(o['fy26_ytd'])}** ({fmt_pct(o['yoy_pct'])} vs the same months of FY25)",
                 f"- Open pipeline: {fmt_money(o['open_pipeline'])} (weighted {fmt_money(o['weighted_pipeline'])})",
                 f"- Declining accounts (≤ {o['declining_threshold_pct']:.0f}% YoY): {o['declining_accounts']}"]
        if up and down and up["id"] != down["id"]:
            lines.append(f"- Fastest-growing {child}: **{up['name']}** ({fmt_pct(up['yoy_pct'])}); "
                         f"weakest: **{down['name']}** ({fmt_pct(down['yoy_pct'])})")
        lines.append(f"- Territory purity: {p['critical']} critical, {p['mixed']} mixed, {p['pure']} pure"
                     + (f", {p['empty']} empty" if p["empty"] else ""))
        if o["plan_moves"]:
            lines.append(f"- Draft FY27 plan: {o['plan_moves']} move(s) so far")
        lines.append("\nDecember isn't closed yet, so year-over-year compares Jan–Nov to Jan–Nov.")
        sugg = [f"Drill into {down['name']}" if down else "Which areas are declining?", "Are my territories pure?",
                "Top accounts by open pipeline"]
        return {"text": "\n".join(lines), "focus": (o["level"], o["id"]), "_results": [o], "suggestions": sugg}

    def _drill(self, e, ctx, tk):
        r = tk.call("drill", entity_id=e["id"])
        if "error" in r:
            return {"text": r["error"], "_results": [r]}
        pre = f"(Closest match for your words: {e['name']}.) " if e.get("fuzzy") else ""
        if r["level"] == "account":
            sig = f" Signal: {r['signal']} ({r['signal_impact']})." if r.get("signal") else ""
            moved = " **Moved in the draft plan.**" if r.get("moved_in_plan") else ""
            text = (f"{pre}**{r['name']}** — {r['industry']}, {r['motion']} account in {r['territory_name']} "
                    f"(owner {r['owner']}).{moved}\n"
                    f"- FY26 Jan–Nov: {fmt_money(r['fy26_ytd'])} ({fmt_pct(r['yoy_pct'])} YoY); FY25 full year {fmt_money(r['fy25_total'])}\n"
                    f"- Open pipeline: {fmt_money(r['open_pipeline'])} across {len(r['opportunities'])} opportunities "
                    f"(weighted {fmt_money(r['weighted_pipeline'])})\n"
                    f"- {r['industry']} FY27 outlook: {fmt_pct(r['industry_outlook_pct'])} (fictional market signal).{sig}")
            if r["motion"] != r["territory_motion"]:
                text += f"\n- ⚠ Motion mismatch: {r['motion']} account in {a_an(r['territory_motion'])} territory."
            return {"text": text, "focus": ("account", r["id"]), "_results": [r],
                    "suggestions": [f"Where should {r['name']} go?", f"Who owns {r['name']}?", "Go up"]}
        child = CHILD[r["level"]]
        kids = r["children"][:5]
        lines = [f"{pre}**{r['name']}** ({r['level']}) — {r['accounts']} accounts, {r['territories']} territories",
                 f"- FY26 Jan–Nov: **{fmt_money(r['fy26_ytd'])}** ({fmt_pct(r['yoy_pct'])} YoY) · open pipeline "
                 f"{fmt_money(r['open_pipeline'])} · declining accounts: {r['declining_accounts']}"]
        if r["level"] == "territory":
            lines.append(f"- Motion: {r['motion']} · owner: {r['owner']}")
        lines.append(f"\nTop {_plural(child)} by revenue:")
        for k in kids:
            extra = f" · {k['motion']}" if k.get("motion") else ""
            lines.append(f"- {k['name']}{extra}: {fmt_money(k['fy26_ytd'])} ({fmt_pct(k.get('yoy_pct'))})")
        sugg = [f"Which {_plural(child)} are declining?", f"Is {r['name']} pure?" if r["level"] != "account" else "Go up", "Go up"]
        if kids:
            sugg.insert(0, f"Drill into {kids[0]['name']}")
        return {"text": "\n".join(lines), "focus": (r["level"], r["id"]), "_results": [r], "suggestions": sugg[:4]}

    def _up(self, ctx, tk):
        focus = ctx.get("focus")
        if not focus or focus[0] in ("bu", tk.persona.level):
            return self._brief(ctx, tk)
        level, eid = focus
        if level == "account":
            parent = tk.accts.loc[tk.accts["account_id"] == eid, "territory_id"].iloc[0]
        elif level == "territory":
            parent = tk.data.territories.loc[tk.data.territories["territory_id"] == eid, "area_id"].iloc[0]
        else:
            parent = tk.data.hierarchy.loc[tk.data.hierarchy["id"] == eid, "parent_id"].iloc[0]
        return self._drill({"id": parent, "name": tk.data.name_of(parent), "level": ""}, ctx, tk)

    def _rank(self, msg, ctx, tk):
        level = next(lvl for lvl, p in _LEVEL_WORDS if re.search(p, msg, re.I))
        low = msg.lower()
        if re.search(r"declin|shrink|drop|worst|slowest|lowest growth", low):
            metric, order = "yoy_pct", "asc"
        elif re.search(r"grow|fastest|best", low):
            metric, order = "yoy_pct", "desc"
        elif "pipeline" in low:
            metric, order = "open_pipeline", "desc"
        elif re.search(r"smallest|lowest|least", low):
            metric, order = "fy26_ytd", "asc"
        else:
            metric, order = "fy26_ytd", "desc"
        ents = resolve_entities(tk.data, msg, ("division", "area", "territory", "bu"))
        within = ents[0]["id"] if ents else (ctx["focus"][1] if ctx.get("focus") and ctx["focus"][0] != "account" else None)
        args = {"level": level, "metric": metric, "order": order, "limit": 5}
        if within:
            args["within_id"] = within
        r = tk.call("rank", **args)
        if "error" in r and within:   # e.g. ranking areas while focused on a territory -> fall back to scope
            args.pop("within_id")
            r = tk.call("rank", **args)
        if "error" in r:
            return {"text": r["error"], "_results": [r]}
        label = {"yoy_pct": "YoY growth (Jan–Nov)", "open_pipeline": "open pipeline", "fy26_ytd": "FY26 Jan–Nov revenue"}[metric]
        lines = [f"**{_plural(level).title()} in {r['within']} by {label}** ({'lowest' if order == 'asc' else 'highest'} first):"]
        for i, x in enumerate(r["rows"], 1):
            lines.append(f"{i}. **{x['name']}** — {fmt_money(x['fy26_ytd'])}, {fmt_pct(x.get('yoy_pct'))} YoY, "
                         f"pipeline {fmt_money(x['open_pipeline'])}")
        if not r["rows"]:
            lines.append("No matching items.")
        sugg = [f"Drill into {r['rows'][0]['name']}"] if r["rows"] else []
        ctx["last_ranked"] = {"level": level, "metric": metric, "order": order,
                              "rows": [{"id": x["id"], "name": x["name"]} for x in r["rows"]]}
        if r["rows"] and metric == "yoy_pct" and order == "asc":
            sugg = [f"Who owns the top declining {level}?"] + sugg
        return {"text": "\n".join(lines), "focus": (r["within_level"], r["within_id"]), "_results": [r],
                "suggestions": sugg + ["Are my territories pure?"]}

    def _purity(self, msg, ctx, tk):
        ents = resolve_entities(tk.data, msg, ("division", "area", "territory", "seller"))
        within = None
        if ents and ents[0]["level"] == "seller":
            t = tk.data.territories[tk.data.territories["owner_id"] == ents[0]["id"]]
            within = None if t.empty else t.iloc[0]["territory_id"]
        elif ents:
            within = ents[0]["id"]
        elif _NEW_TERR.search(msg):
            within = self._last_receiving_territory(ctx, tk)
        if within is None and not ents and not _NEW_TERR.search(msg):
            within = (ctx["focus"][1] if ctx.get("focus") and ctx["focus"][0] in ("division", "area", "territory")
                      and ctx["focus"][1] != tk.persona.scope_id else None)
        if within is None and _NEW_TERR.search(msg):
            return {"text": "Which territory? I don't see a recent move — try *“Is Dana Pemberton's territory pure?”*.",
                    "suggestions": ["Are my territories pure?"]}
        if within and tk.data.territories["territory_id"].eq(within).any():
            return self._territory_purity(within, ctx, tk)
        rep = tk.call("purity_report", **({"within_id": within} if within else {}))
        if "error" in rep:
            return {"text": rep["error"], "_results": [rep]}
        total = len(rep["territories"])
        lines = [f"**Territory purity — {rep['within']}**: {rep['pure']} of {total} territories are pure; "
                 f"{rep['critical']} critical, {rep['mixed']} mixed" + (f", {rep['empty']} empty" if rep["empty"] else "") + ".",
                 "Pure = every account shares the territory's sales motion (Deep / Expansion / Greenfield)."]
        worst = [t for t in rep["territories"] if t["status"] in ("Critical", "Mixed")][:4]
        for t in worst:
            lines.append(f"- **{t['territory_name']}** ({t['motion']}): {t['matching']}/{t['accounts']} accounts match; "
                         f"misfits are {t['misfit_motions']} — {t['status'].lower()}")
        fixes = tk.call("purity_fixes", limit=3, **({"within_id": within} if within else {})) if rep["misfits"] else {"fixes": []}
        props = fixes.get("fixes", [])
        if props:
            lines.append("\nSuggested fixes (largest accounts first) — review each and apply if you agree:")
            for f in props:
                lines.append(f"- Move **{f['account_name']}** ({f['account_motion']}, {fmt_money(f['fy26_ytd'])}) from "
                             f"{f['from_territory_name']} → **{f['to_territory_name']}**: *{f['verdict']}*")
        lines.append(f"\n_{rep['rules_label']}_")
        return {"text": "\n".join(lines), "_results": [rep, fixes], "proposals": props,
                "suggestions": ["Show my changes", "Brief me"]}

    def _last_receiving_territory(self, ctx, tk):
        if ctx.get("last_move_to"):
            return ctx["last_move_to"]
        r = tk.call("plan_changes")
        if r.get("moves"):
            return r["moves"][-1]["to_territory_id"]
        if ctx.get("last_proposals"):
            return ctx["last_proposals"][0][1]
        return None

    def _territory_purity(self, tid, ctx, tk):
        rep = tk.call("purity_report", within_id=tid)
        if "error" in rep:
            return {"text": rep["error"], "_results": [rep]}
        t = rep["territories"][0]
        lo, hi = tk.rules.capacity(t["motion"])
        lines = [f"**{t['territory_name']}** ({t['motion']}, owner {t['owner']}): "
                 + ("✅ **pure** — every account matches the territory's sales motion." if t["status"] == "Pure"
                    else f"⚠ **not pure** — {t['matching']} of {t['accounts']} accounts match; "
                         f"misfit motion(s): {t['misfit_motions']}.")]
        if t["accounts"] > hi:
            lines.append(f"- Load: {t['accounts']} accounts vs a {lo}–{hi} guide for {a_an(t['motion'])} AE — over capacity.")
        else:
            lines.append(f"- Load: {t['accounts']} accounts (guide {lo}–{hi}).")
        mis = [m for m in rep["misfits"] if m["territory_id"] == tid]
        for m in mis[:4]:
            lines.append(f"- Misfit: **{m['account_name']}** is {a_an(m['account_motion'])} account ({fmt_money(m['fy26_ytd'])} Jan–Nov).")
        fixes = tk.call("purity_fixes", limit=3, within_id=tid) if mis else {"fixes": []}
        props = fixes.get("fixes", [])
        if props:
            lines.append("\n**How to fix it** — move the misfit(s) to a same-area territory with the matching motion:")
            for f in props:
                lines.append(f"- **{f['account_name']}** → **{f['to_territory_name']}** ({f['to_owner']}): *{f['verdict']}*")
            after = t["accounts"] - len(props)
            lines.append(f"\nAfter the fix: {t['territory_name']} would be pure with {after} accounts"
                         + (f" (back within the {lo}–{hi} guide)." if lo <= after <= hi else "."))
        elif mis:
            lines.append("\nNo same-area territory with the matching motion exists — consider an overlay or reclassifying the account.")
        return {"text": "\n".join(lines), "_results": [rep, fixes], "proposals": props, "focus": ("territory", tid),
                "suggestions": (["Should I implement the fix?"] if props else []) + ["Show my changes"]}

    def _should(self, msg, ctx, tk):
        pairs = list(ctx.get("last_proposals") or [])[:3]
        if not pairs:
            return {"text": "I haven't proposed anything to implement yet. Ask *“Are my territories pure?”* or "
                            "*“Where should <account> go?”* first.", "suggestions": ["Are my territories pure?"]}
        evs = [tk.call("evaluate_move", account_id=a, to_territory_id=t) for a, t in pairs]
        bad = [e for e in evs if "error" in e]
        evs = [e for e in evs if "error" not in e]
        if not evs:
            return {"text": "That's already done — " + bad[0]["error"].replace(" is already in ", " is already in ")
                            + " in your draft plan. Ask *“Show my changes”* to review it.", "_results": bad,
                    "suggestions": ["Show my changes"]}
        imp = tk.call("revenue_impact", account_ids=[e["account_id"] for e in evs],
                      to_territory_ids=[e["to_territory_id"] for e in evs])
        rec = [e for e in evs if e["verdict"] == "Recommended"]
        if len(rec) == len(evs):
            head = "**My recommendation: yes — implement it.** Every check below is green or neutral."
        elif not rec:
            head = "**My recommendation: not as proposed.** The checks below raise concerns — review them first."
        else:
            head = "**My recommendation: implement the recommended move(s) only** — see each verdict below."
        lines = [head]
        for e in evs:
            pros = [r["finding"] for r in e["reasons"] if r["impact"] > 0][:2]
            cons = [r["finding"] for r in e["reasons"] if r["impact"] < 0][:2]
            lines.append(f"- **{e['account_name']}** → {e['to_territory_name']}: *{e['verdict']}* (score {e['score']:+d})")
            lines += [f"  - ＋ {x}" for x in pros] + [f"  - － {x}" for x in cons]
        if "error" not in imp:
            lines.append(f"- FY27 company effect (estimate): **{_sgn(imp['company_delta'])}** "
                         f"(range {_sgn(imp['company_delta_low'])} to {_sgn(imp['company_delta_high'])}).")
        lines.append("\n**It's your call.** Open the proposal below, add your reason and click **Apply to draft plan** — "
                     "I never change the plan myself, and every applied move can be reverted.")
        return {"text": "\n".join(lines), "_results": evs + [imp], "proposals": evs,
                "suggestions": ["Show my changes", "What's the revenue impact of my plan?"]}

    def _move(self, msg, ctx, tk):
        accts = resolve_entities(tk.data, msg, ("account",))
        if not accts and ctx.get("focus") and ctx["focus"][0] == "account":
            accts = [{"id": ctx["focus"][1], "name": tk.data.name_of(ctx["focus"][1]), "level": "account"}]
        if not accts:
            return {"text": "Which account should move? Try *“Move Selridge Energy to Southern California Deep 1”* "
                            "or open an account first."}
        a = accts[0]
        rest = msg
        for name in (a["name"], a["id"]):
            rest = re.sub(re.escape(name), " ", rest, flags=re.I)
        targets = resolve_entities(tk.data, rest, ("territory", "seller"))
        if not targets:
            return self._where(f"where should {a['name']} go", tk)
        t = targets[0]
        tid = t["id"]
        if t["level"] == "seller":
            terr = tk.data.territories[tk.data.territories["owner_id"] == t["id"]]
            if terr.empty:
                return {"text": f"{t['name']} doesn't own a territory, so accounts can't be assigned to them directly. "
                                f"Pick an Account Executive or a territory."}
            tid = terr.iloc[0]["territory_id"]
        ev = tk.call("evaluate_move", account_id=a["id"], to_territory_id=tid)
        if "error" in ev:
            return {"text": ev["error"], "_results": [ev]}
        return {"text": self._eval_text(ev), "_results": [ev], "proposals": [ev], "focus": ("account", a["id"]),
                "suggestions": [f"Where should {ev['account_name']} go?", "Show my changes"]}

    def _where(self, msg, tk):
        accts = resolve_entities(tk.data, msg, ("account",))
        if not accts:
            return {"text": "Tell me which account — e.g. *“Where should Selridge Energy go?”*"}
        r = tk.call("suggest_targets", account_id=accts[0]["id"])
        if "error" in r:
            return {"text": r["error"], "_results": [r]}
        if not r["options"]:
            return {"text": f"I found no other territory in your scope for {r['account_name']}.", "_results": [r]}
        lines = [f"**Best-fit territories for {r['account_name']}** (within your access):"]
        for o in r["options"]:
            lines.append(f"- **{o['to_territory_name']}** ({o['to_motion']}, owner {o['to_owner']}): *{o['verdict']}* "
                         f"(score {o['score']:+d}) — {o['reasons'][0]['finding']}")
        lines.append("\nOpen any option below to see all reasons, then apply it to the draft plan if you agree.")
        return {"text": "\n".join(lines), "_results": [r], "proposals": r["options"],
                "focus": ("account", r["account_id"])}

    def _who_owns(self, msg, ctx, tk):
        accts = resolve_entities(tk.data, msg, ("account", "territory", "area", "division"))
        ranked = ctx.get("last_ranked")
        if not accts and ranked and ranked["rows"]:
            top = ranked["rows"][0]
            accts = [{"id": top["id"], "name": top["name"], "level": ranked["level"]}]
        if not accts:
            return {"text": "Which account, territory or area? For example *“Who owns Selridge Energy?”*"}
        e = accts[0]
        if e["level"] in ("area", "division") or (e["level"] == "territory" and re.search(r"declin|drop|shrink", msg, re.I)):
            return self._decline_owner(e, ctx, tk)
        r = tk.call("drill", entity_id=e["id"])
        if "error" in r:
            return {"text": r["error"], "_results": [r]}
        if r["level"] == "account":
            ctx["subject"] = ("seller", tk.accts.loc[tk.accts["account_id"] == r["id"], "owner_id"].iloc[0])
            return {"text": f"**{r['name']}** is owned by **{r['owner']}** ({r['territory_name']}, {r['area']}).",
                    "_results": [r], "focus": ("account", r["id"]),
                    "suggestions": [f"Show {r['owner']}'s book", f"What's the pipeline for {r['name']}?"]}
        ctx["subject"] = ("seller", tk.data.territories.loc[tk.data.territories["territory_id"] == r["id"], "owner_id"].iloc[0])
        return {"text": f"**{r['name']}** is owned by **{r['owner']}** ({r['motion']} territory).", "_results": [r],
                "focus": ("territory", r["id"]), "suggestions": ["Show me their entire book"]}

    def _decline_owner(self, e, ctx, tk):
        r = tk.call("decline_drivers", within_id=e["id"], limit=3)
        if "error" in r:
            return {"text": r["error"], "_results": [r]}
        lines = [f"**{r['within']}** ({r['level']}, {fmt_pct(r['yoy_pct'])} YoY Jan–Nov) is led by **{r['leader']}**"
                 f"{' (' + r['leader_role'] + ')' if r.get('leader_role') else ''}."]
        if r["sellers"]:
            top = r["sellers"][0]
            lines.append(f"The decline sits mostly with **{top['seller_name']}** ({top['territory_name']}): "
                         f"**{fmt_money(top['change'])}** across {top['declining_accounts']} declining accounts — "
                         f"{top['share_of_decline_pct']:.1f}% of the revenue lost in declining accounts.")
            if r["accounts"]:
                lines.append("Biggest drops: " + "; ".join(f"{a['name']} {fmt_money(a['change'])} ({fmt_pct(a['yoy_pct'])})"
                                                          for a in r["accounts"]) + ".")
            ctx["subject"] = ("seller", top["seller_id"])
            sugg = ["Show me their entire book", f"Drill into {r['within']}"]
        else:
            lines.append("No account in it is declining year over year.")
            sugg = [f"Drill into {r['within']}"]
        return {"text": "\n".join(lines), "_results": [r], "focus": (r["level"], r["within_id"]), "suggestions": sugg}

    def _pipeline(self, msg, ctx, tk):
        ents = resolve_entities(tk.data, msg, ("account", "seller", "territory", "area", "division"))
        if ents:
            eid = ents[0]["id"]
        elif ctx.get("subject"):
            eid = ctx["subject"][1]
        elif ctx.get("focus"):
            eid = ctx["focus"][1]
        else:
            eid = tk.persona.scope_id
        r = tk.call("pipeline_summary", entity_id=eid, limit=4)
        if "error" in r:
            return {"text": r["error"], "_results": [r]}
        what = f"{r['entity']}'s book" if r["level"] == "seller" else r["entity"]
        lines = [f"**Open pipeline — {what}**: **{fmt_money(r['open_pipeline'])}** across {r['opportunities']} opportunities "
                 f"in {r['accounts']} account(s); weighted {fmt_money(r['weighted_pipeline'])}.",
                 f"- Late-stage closing {'/'.join(r['near_term_periods'])}: {fmt_money(r['near_term_late_stage'])} "
                 f"— the part most at risk if the owner changes."]
        if r["by_stage"]:
            lines.append("- By stage: " + ", ".join(f"{x['stage']} {fmt_money(x['amount'])}" for x in r["by_stage"][:4]))
        if r["top"]:
            lines.append("\nLargest opportunities:")
            for o in r["top"]:
                lines.append(f"- {o['account_name']}: {fmt_money(o['amount'])} · {o['stage']} ({o['probability_pct']:.0f}% probability) · closes {o['close_period']}")
        sugg = ["Move one of these accounts to another seller", "Are my territories pure?"] if r["level"] == "seller" else ["Brief me"]
        return {"text": "\n".join(lines), "_results": [r], "suggestions": sugg}

    def _book(self, e, ctx, tk):
        r = tk.call("seller_book", seller_id=e["id"])
        if "error" in r:
            return {"text": r["error"], "_results": [r]}
        ctx["subject"] = ("seller", r["seller_id"])
        lines = [f"**{r['seller_name']}** ({r['role']}) — {r.get('territory_name') or 'no territory'}: "
                 f"{len(r['accounts'])} accounts, {fmt_money(r['fy26_ytd'])} FY26 Jan–Nov "
                 f"({fmt_pct(r.get('yoy_pct'))} YoY), pipeline {fmt_money(r['open_pipeline'])}."]
        for x in r["accounts"][:8]:
            lines.append(f"- {x['name']} ({x['motion']}): {fmt_money(x['fy26_ytd'])}, {fmt_pct(x['yoy_pct'])} YoY")
        flags = []
        if r.get("over_capacity"):
            flags.append(f"⚠ **Over capacity:** {len(r['accounts'])} accounts vs a {r['capacity_low']}–{r['capacity_high']} "
                         f"guide for {a_an(r['territory_motion'])} AE.")
        if r.get("misfits"):
            flags.append("⚠ **Motion misfit:** " + ", ".join(f"{m['name']} ({m['motion']})" for m in r["misfits"])
                         + f" in {a_an(r['territory_motion'])} territory.")
        if flags:
            lines.append("")
            lines += flags
        focus = ("territory", r["territory_id"]) if r.get("territory_id") else None
        sugg = ["What's the opportunity pipeline for it?"]
        if r.get("misfits"):
            sugg.append(f"Where should {r['misfits'][0]['name']} go?")
        return {"text": "\n".join(lines), "_results": [r], "focus": focus, "suggestions": sugg}

    def _impact(self, msg, ctx, tk):
        """FY27 revenue impact of: a named move, the last proposals, or the draft plan (in that order)."""
        accts = resolve_entities(tk.data, msg, ("account",))
        pairs, source = [], ""
        if accts:
            a = accts[0]
            rest = re.sub(re.escape(a["name"]), " ", msg, flags=re.I)
            terr = resolve_entities(tk.data, rest, ("territory",))
            last = dict(ctx.get("last_proposals") or [])
            if terr:
                pairs, source = [(a["id"], terr[0]["id"])], "the move you described"
            elif a["id"] in last:
                pairs, source = [(a["id"], last[a["id"]])], "the move I just suggested"
        if not pairs and not _PLAN_WORDS.search(msg) and re.search(r"\b(purity|pure|fix\w*)\b", msg, re.I):
            fx = tk.call("purity_fixes", limit=5)
            pairs = [(f["account_id"], f["to_territory_id"]) for f in fx.get("fixes", [])]
            source = "the top purity fixes"
            if pairs:
                ctx["last_proposals"] = pairs
        if not pairs and not _PLAN_WORDS.search(msg) and ctx.get("last_proposals"):
            pairs, source = list(ctx["last_proposals"]), "the moves I just suggested"
        if pairs:
            r = tk.call("revenue_impact", account_ids=[p[0] for p in pairs], to_territory_ids=[p[1] for p in pairs])
        else:
            r = tk.call("plan_revenue_impact")
            source = "your draft FY27 plan"
            if "error" not in r and not r.get("move_count"):
                return {"text": "There's nothing to estimate yet: no moves were just suggested and your draft plan is "
                                "empty. Ask *“Are my territories pure?”* or *“Move <account> to <territory>”* first, "
                                "then ask about the FY27 impact.", "_results": [r],
                        "suggestions": ["Are my territories pure?", "Brief me"]}
        if "error" in r:
            return {"text": r["error"], "_results": [r]}
        moved = r.get("fy27_moved_revenue", sum(i["fy27_if_move"] for i in r["items"]))
        lines = [f"**Estimated FY27 revenue impact of {source}** ({len(r['items'])} move(s)):",
                 f"- **Revenue that changes hands: {fmt_money(moved)}** in FY27 — it moves between territories/owners, "
                 f"so at company level it nets out.",
                 f"- **Company-level effect: {_sgn(r['company_delta'])}** (likely), range "
                 f"{_sgn(r['company_delta_low'])} to {_sgn(r['company_delta_high'])} — from coverage changes "
                 f"and pipeline put at risk by owner changes.", ""]
        lines += [f"- {x}" for x in summary_lines(r)[:6]]
        if r["territory_shifts"]:
            lines.append("\nBiggest territory shifts (FY27):")
            shifts = r["territory_shifts"]
            for t in (shifts if len(shifts) <= 4 else shifts[:2] + shifts[-2:]):
                lines.append(f"- {t['territory_name']}: {'+' if t['fy27_change'] >= 0 else ''}{fmt_money(t['fy27_change'])}")
        lines.append("\n_Estimate on synthetic data: December from FY25 seasonality, fictional industry outlook, "
                     "illustrative coverage and disruption factors. Quotas come in Phase 3._")
        return {"text": "\n".join(lines), "_results": [r],
                "suggestions": ["Show my changes", "Are my territories pure?", "Brief me"]}

    def _changes(self, tk):
        r = tk.call("plan_changes")
        if not r["count"]:
            return {"text": "No changes in your draft FY27 plan yet.", "_results": [r]}
        lines = [f"**Draft FY27 plan — {r['count']} move(s) in your scope:**"]
        for m in r["moves"][-10:]:
            lines.append(f"- {tk.data.name_of(m['account_id'])}: {tk.data.name_of(m['from_territory_id'])} → "
                         f"{tk.data.name_of(m['to_territory_id'])} ({m['advisor_verdict']}; by {m['by'].split(' · ')[0]}) — “{m['reason']}”")
        return {"text": "\n".join(lines), "_results": [r]}

    def _undo_hint(self, tk):
        r = tk.call("plan_changes")
        if not r["count"]:
            return {"text": "There's nothing to undo — the draft plan has no moves.", "_results": [r]}
        m = r["moves"][-1]
        return {"text": f"The latest move is **{tk.data.name_of(m['account_id'])}** → {tk.data.name_of(m['to_territory_id'])}. "
                        f"Use **Revert** next to it in *Plan moves* — I don't change the plan myself.", "_results": [r]}

    def _help(self):
        return {"text": "I'm your planning advisor for FY27. Try:\n- *Brief me*\n- *Drill into Pacific Northwest*\n"
                        "- *Which accounts are declining?* (applies to where you are)\n- *Top accounts by open pipeline*\n"
                        "- *Move Selridge Energy to Southern California Deep 1* (I'll advise; you decide)\n"
                        "- *Where should Selridge Energy go?*\n- *Are my territories pure?*\n- *Show my changes*",
                "suggestions": ["Brief me", "Are my territories pure?"]}

    @staticmethod
    def _eval_text(ev: dict[str, Any]) -> str:
        icon = {"Recommended": "✅", "Review with caution": "⚠️", "Not recommended": "⛔"}[ev["verdict"]]
        lines = [f"{icon} **{ev['verdict']}** (score {ev['score']:+d}): move **{ev['account_name']}** "
                 f"({ev['account_motion']}, {fmt_money(ev['fy26_ytd'])} Jan–Nov) from {ev['from_territory_name']} "
                 f"({ev['from_owner']}) → **{ev['to_territory_name']}** ({ev['to_owner']})."]
        for r in ev["reasons"]:
            mark = "＋" if r["impact"] > 0 else "－" if r["impact"] < 0 else "•"
            lines.append(f"- {mark} **{r['factor']}**: {r['finding']}")
        lines.append(f"\n{ev['note']}")
        return "\n".join(lines)
