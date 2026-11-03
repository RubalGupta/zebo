"""Norbae AI — FY27 Planning Cockpit (Phase 1).

Run:  streamlit run streamlit_app.py      (Windows: double-click run_streamlit.bat)

Layout: a compact planning workspace with an "Advisor insights" strip on every step, and the Planning Advisor as a
floating 🤖 agent (bottom-right) that opens on click. All numbers come from src/planning (deterministic, tested).
The Advisor engine is pluggable (rules by default; Claude API optional). Source data in data/fy27 is read-only;
plan changes are saved to a draft plan under local_state/ with who/when/why.
"""
from __future__ import annotations

import html
import os
import sys
import tempfile
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from advisor.claude_engine import get_advisor  # noqa: E402
from advisor.tools import PlanningToolkit  # noqa: E402
from planning.advice import evaluate_move, purity_fix, purity_report, suggest_targets, territory_load  # noqa: E402
from planning.analysis import a_an, account_detail, entity_level, fmt_money, fmt_pct, summarize  # noqa: E402
from planning.config import DEFAULT_RULES, LABEL  # noqa: E402
from planning.data import PlanningDataError, load  # noqa: E402
from planning.impact import account_move_impact  # noqa: E402
from planning.plan_store import PlanStore, effective_accounts  # noqa: E402
from planning.scope import CHILD, filter_scope, persona_by_id, personas  # noqa: E402

st.set_page_config(page_title="Zebo Planner", page_icon="🧭", layout="wide",
                   initial_sidebar_state="expanded")

# Norbae AI brand palette (from norbae-ai.lovable.app)
INK, MUTED, ACCENT, WARN = "#0a1f44", "#4a5878", "#2f55d4", "#c0305f"
CYAN, MINT, BLUE, PANEL, ORANGE = "#22d3ee", "#10d9a0", "#2f80ff", "#0a1530", "#ff8a1f"
st.markdown(f"""<style>
@import url('https://fonts.googleapis.com/css2?family=Lexend:wght@300;400;500;600;700&display=swap');
html {{ font-size: 14.5px; }}
html, body, .stApp, .stApp p, .stApp li, .stApp label, .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5,
.stApp input, .stApp textarea, .stApp button, .stApp summary, .stApp [data-testid="stMarkdownContainer"] {{
  font-family: 'Lexend', system-ui, sans-serif; }}
/* keep Streamlit's icon font for icons (otherwise they show as words like "arrow_right") */
.stApp [data-testid="stIconMaterial"], .stApp span[class*="material"], .stApp .material-symbols-rounded {{
  font-family: 'Material Symbols Rounded' !important; }}
[data-testid="stAppViewContainer"] {{ background: #ffffff; }}
.block-container {{ padding: 1.1rem 1.6rem 6rem; max-width: 1400px; }}
/* sidebar = brand dark panel */
[data-testid="stSidebar"] {{ min-width: 260px; max-width: 280px; background: {PANEL}; }}
[data-testid="stSidebar"] .block-container, [data-testid="stSidebarContent"] {{ padding-top: .6rem; }}
[data-testid="stSidebar"] h3, [data-testid="stSidebar"] p, [data-testid="stSidebar"] label,
[data-testid="stSidebar"] span, [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] {{ color: #f2f5ff; }}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"], [data-testid="stSidebar"] small {{ color: #b7c2de !important; }}
[data-testid="stSidebar"] [data-baseweb="select"] span {{ color: {INK}; }}
[data-testid="stSidebar"] hr {{ border-color: #ffffff26; }}
h4 {{ margin: .2rem 0 .4rem !important; color: {INK}; }}
.nb-top {{ display:flex; align-items:baseline; gap:.75rem; flex-wrap:wrap; margin-bottom:.2rem; }}
.nb-title {{ font-size: 1.4rem; font-weight: 700; color:{INK}; margin:0; letter-spacing:-.01em; }}
.nb-chip {{ font-size:.74rem; font-weight:600; padding:.12rem .6rem; border-radius:999px; background:#22d3ee29;
  color:#0e7490; border:1px solid #22d3ee66; white-space:nowrap; }}
.nb-chip.demo {{ background:#ff8a1f1f; color:#9a4a00; border-color:#ff8a1f55; }}
.nb-sub {{ color:{MUTED}; font-size:.82rem; margin:.1rem 0 .7rem; }}
.nb-kpis {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(145px, 1fr)); gap:.6rem; margin:.3rem 0 .8rem; }}
.nb-kpi {{ background:#f8fafe; border:1px solid #e1e6f2; border-radius:12px; padding:.6rem .85rem; min-width:0;
  box-shadow: 0 6px 18px #0a1f440d; }}
.nb-kpi .l {{ color:{MUTED}; font-size:.7rem; font-weight:600; text-transform:uppercase; letter-spacing:.05em; }}
.nb-kpi .v {{ font-size:1.35rem; font-weight:700; color:{INK}; font-variant-numeric:tabular-nums; line-height:1.25; }}
.nb-kpi .s {{ font-size:.76rem; color:{MUTED}; }}
.nb-kpi .s.up {{ color:#0a8f6c; font-weight:600; }} .nb-kpi .s.down {{ color:{WARN}; font-weight:600; }}
.nb-insights {{ background:#f4f7ff; border:1px solid #e1e6f2; border-radius:14px; padding:.7rem .9rem .55rem;
  margin:.2rem 0 .9rem; position:relative; overflow:hidden; }}
.nb-insights::before {{ content:""; position:absolute; left:0; right:0; top:0; height:3px;
  background: linear-gradient(90deg, {CYAN}, {BLUE}); }}
.nb-insights .h {{ font-weight:600; color:#0e7490; font-size:.8rem; letter-spacing:.06em; text-transform:uppercase;
  margin-bottom:.35rem; }}
.nb-insights ul {{ margin:0; padding-left:1.1rem; font-size:.86rem; color:{INK}; }} .nb-insights li {{ margin:.12rem 0; }}
.nb-crumb {{ color:{MUTED}; font-size:.84rem; margin:.1rem 0 .3rem; }} .nb-crumb b {{ color:{INK}; }}
/* buttons: brand primary = cyan→mint gradient with navy text */
button[kind="primary"], button[data-testid="stBaseButton-primary"], [data-testid="stFormSubmitButton"] button {{
  background: linear-gradient(90deg, {CYAN}, {MINT}) !important; color: {PANEL} !important; border: 0 !important;
  font-weight: 600 !important; }}
button[kind="secondary"], button[data-testid="stBaseButton-secondary"] {{ border-color: #c9d1e3 !important; color: {INK} !important; }}
button[kind="secondary"]:hover, button[data-testid="stBaseButton-secondary"]:hover {{ border-color: {ACCENT} !important; color: {ACCENT} !important; }}
/* floating Advisor */
.st-key-advisor_fab {{ position: fixed; right: 26px; bottom: 22px; z-index: 1000; width: auto !important; }}
.st-key-advisor_fab button {{ border-radius: 999px !important; padding: .6rem 1.15rem !important;
  background: linear-gradient(90deg, {CYAN}, {MINT}) !important; color: {PANEL} !important; border: 0 !important;
  box-shadow: 0 10px 30px #22d3ee66; font-weight: 700 !important; }}
.nb-teaser {{ position: fixed; right: 26px; bottom: 86px; z-index: 999; max-width: 320px; background:{PANEL};
  border:1px solid #ffffff26; border-radius: 14px 14px 4px 14px; padding:.65rem .85rem; font-size:.84rem; color:#f2f5ff;
  box-shadow: 0 24px 60px #0a153059; }}
.nb-teaser b {{ color:{CYAN}; }} .nb-teaser .m {{ color:#b7c2de; }}
div[data-testid="stPopoverBody"], div[data-testid="stPopover"] div[data-baseweb="popover"] > div {{
  width: min(470px, 92vw) !important; max-width: min(470px, 92vw) !important; }}
</style>""", unsafe_allow_html=True)

STEPS = [
    ("business", "1 · My business", 1), ("accounts", "2 · Accounts & owners", 1), ("moves", "3 · Plan moves", 1),
    ("purity", "4 · Territory purity", 1), ("hierarchy", "5 · Hierarchy & comp", 2), ("revenue", "6 · Revenue lock", 2),
    ("quota", "7 · Quota model", 3), ("seller", "8 · Seller view & disputes", 3),
]
STEP_LABEL = {k: f"{lbl}{'' if ph == 1 else f'  · Phase {ph}'}" for k, lbl, ph in STEPS}
VERDICT_ICON = {"Recommended": "✅", "Review with caution": "⚠️", "Not recommended": "⛔"}
MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def md(text: str) -> str:
    """Markdown-safe text: escape $ so Streamlit doesn't render currency as LaTeX math."""
    return str(text).replace("$", "\\$")


def esc(text) -> str:
    """HTML-escape for unsafe_allow_html blocks; $ becomes an entity so it is never parsed as LaTeX."""
    return html.escape(str(text)).replace("$", "&#36;")


def kpi_tiles(items: list[tuple]) -> None:
    """items: (label, value, sub, tone) with tone in {'', 'up', 'down'}"""
    cells = "".join(f'<div class="nb-kpi"><div class="l">{esc(l)}</div><div class="v">{esc(v)}</div>'
                    f'<div class="s {t}">{esc(s)}</div></div>' for l, v, s, t in items)
    st.markdown(f'<div class="nb-kpis">{cells}</div>', unsafe_allow_html=True)


def tone(v) -> str:
    return "" if v is None or pd.isna(v) else ("up" if v >= 0 else "down")


# ------------------------------------------------------------------ data & state
@st.cache_resource(show_spinner="Loading planning data…")
def get_data():
    return load()


try:
    DATA = get_data()
except PlanningDataError as exc:
    st.error(f"Planning data problem: {exc}")
    st.stop()

ss = st.session_state


def _running_locally() -> bool:
    """True on localhost. When the app is hosted (e.g. Streamlit Community Cloud), every visitor gets a private plan."""
    if os.environ.get("NORBAE_PER_SESSION_PLAN") == "1":
        return False
    if os.environ.get("NORBAE_SHARED_PLAN") == "1":
        return True
    try:
        host = (st.context.headers.get("Host") or "").split(":")[0].strip("[]").lower()
    except Exception:
        return True
    return host in ("", "localhost", "127.0.0.1", "::1")


if _running_locally():
    STORE = PlanStore()                      # local_state/ — survives restarts on your own machine
else:
    if "plan_dir" not in ss:                 # hosted: a fresh, private draft plan per browser session
        ss.plan_dir = tempfile.mkdtemp(prefix="zebo_plan_")
    STORE = PlanStore(ss.plan_dir)
PERSONAS = personas(DATA)
DEFAULT_PERSONA = next((p.id for p in PERSONAS if p.level == "division"), PERSONAS[0].id)
ss.setdefault("persona_id", DEFAULT_PERSONA)
ss.setdefault("messages", [])
ss.setdefault("ctx", {"focus": None})
ss.setdefault("pending_prompt", None)
ss.setdefault("selected_account", None)
ss.setdefault("teaser", True)
if "goto_step" in ss:
    ss["step"] = ss.pop("goto_step")
ss.setdefault("step", "business")


def persona():
    return persona_by_id(DATA, ss.persona_id)


def toolkit():
    return PlanningToolkit(DATA, STORE, persona(), DEFAULT_RULES)


ADVISOR = get_advisor(toolkit)


def accts_all():
    return effective_accounts(DATA, STORE)


def scoped_accounts():
    return filter_scope(persona(), accts_all())


def scoped_territories():
    return filter_scope(persona(), DATA.territories)


def open_advisor() -> None:
    try:
        ss["advisor_pop"] = True
    except Exception:   # older Streamlit without popover state
        pass


def ask(text: str, from_workspace: bool = False) -> None:
    ss.messages.append({"role": "user", "text": text})
    r = ADVISOR.respond(text, ss.ctx)
    ss.messages.append({"role": "assistant", **r})
    ss.teaser = False
    if r.get("focus"):
        ss.ctx["focus"] = r["focus"]
        if r["focus"][0] == "account":
            ss.selected_account = r["focus"][1]
    if from_workspace:
        open_advisor()
        st.toast("🤖 The Advisor answered — see the chat (bottom right).")


def on_persona_change() -> None:
    ss.messages, ss.ctx, ss.selected_account, ss.teaser = [], {"focus": None}, None, True


def apply_move(ev: dict, reason: str) -> None:
    # Re-validate scope and re-evaluate at apply time; the recorded verdict is the current one.
    tk = toolkit()
    fresh = tk.call("evaluate_move", account_id=ev["account_id"], to_territory_id=ev["to_territory_id"])
    if "error" in fresh:
        st.error(fresh["error"])
        return
    try:
        mv = STORE.add_move(account_id=fresh["account_id"], from_territory_id=fresh["from_territory_id"],
                            to_territory_id=fresh["to_territory_id"], from_owner_id=fresh["from_owner_id"],
                            to_owner_id=fresh["to_owner_id"], reason=reason, verdict=fresh["verdict"],
                            score=fresh["score"], by=persona().label)
    except ValueError as exc:
        st.error(str(exc))
        return
    ss.messages.append({"role": "assistant", "engine": "Plan", "text":
                        f"Applied to the **draft FY27 plan** ({mv['move_id']}): {fresh['account_name']} → "
                        f"{fresh['to_territory_name']}. Recorded with your reason and the Advisor's verdict "
                        f"(*{fresh['verdict']}*). Source data is unchanged; you can revert this in *Plan moves*.",
                        "tool_calls": [], "proposals": [], "suggestions": ["Show my changes", "Are my territories pure?"]})
    ss.ctx["last_move_to"] = fresh["to_territory_id"]   # so "is the new territory pure?" knows which one
    st.toast(f"✅ Added to draft plan: {fresh['account_name']} → {fresh['to_territory_name']}")
    st.rerun()


def render_proposal(ev: dict, key: str, expanded: bool = False) -> None:
    icon = VERDICT_ICON.get(ev["verdict"], "")
    title = f"{icon} {ev['verdict']} · {ev['account_name']} → {ev['to_territory_name']} (score {ev['score']:+d})"
    with st.expander(md(title), expanded=expanded):
        st.caption(md(f"From {ev['from_territory_name']} ({ev['from_owner']}) → {ev['to_territory_name']} "
                      f"({ev['to_owner']}) · {ev['account_motion']} account · {fmt_money(ev['fy26_ytd'])} Jan–Nov"))
        for r in ev["reasons"]:
            mark = "🟢" if r["impact"] > 0 else "🔴" if r["impact"] < 0 else "⚪"
            st.markdown(md(f"{mark} **{r['factor']}** — {r['finding']}"))
        im = account_move_impact(DATA, accts_all(), ev["account_id"], ev["to_territory_id"], DEFAULT_RULES)
        if "error" not in im:
            sg = lambda v: ("+" if v >= 0 else "") + fmt_money(v)
            st.markdown(md(f"📈 **FY27 revenue (estimate):** {fmt_money(im['fy27_if_move'])} moves to "
                           f"{im['to_territory_name']} (vs {fmt_money(im['fy27_if_stay'])} if it stays) · company effect "
                           f"**{sg(im['company_delta'])}** (range {sg(im['company_delta_low'])} to {sg(im['company_delta_high'])})"))
        st.caption(md(f"{ev['note']} {ev['rules_label']}"))
        reason = st.text_input("Your reason (required to apply)", key=f"reason_{key}",
                               placeholder="e.g. Align to Deep motion; keep current AE as overlay until Q1 close")
        if st.button("Apply to draft plan", key=f"apply_{key}", type="primary", disabled=not reason.strip()):
            apply_move(ev, reason)


def insights(lines: list[str], actions: list[tuple[str, str]] | None = None, key: str = "ins") -> None:
    """Advisor insight strip shown on every step; each action asks the Advisor a follow-up."""
    items = "".join(f"<li>{x}</li>" for x in lines)
    st.markdown(f'<div class="nb-insights"><div class="h">🤖 Advisor insights</div><ul>{items}</ul></div>',
                unsafe_allow_html=True)
    if actions:
        cols = st.columns(len(actions))
        for i, (label, prompt) in enumerate(actions):
            if cols[i].button(label, key=f"{key}_{i}", width="stretch"):
                ask(prompt, from_workspace=True)


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.markdown("### 🧭 Norbae AI")
    st.caption("Zebo Planner")
    st.selectbox("Viewing as", [p.id for p in PERSONAS], key="persona_id", on_change=on_persona_change,
                 format_func=lambda pid: next(p.label for p in PERSONAS if p.id == pid))
    st.radio("Planning steps", [k for k, _, _ in STEPS], key="step", format_func=lambda k: STEP_LABEL[k])
    n_moves = len([m for m in STORE.moves() if m["account_id"] in set(scoped_accounts()["account_id"])])
    st.caption(f"Draft FY27 plan: **{n_moves}** move(s) in your scope")
    if ADVISOR.name.startswith("Rule"):
        st.caption("🤖 Advisor brain: **built-in rules** (free, offline). It understands planning questions it was "
                   "built for; switch to the Claude API later for open-ended conversation.")
    else:
        st.caption(f"🤖 Advisor brain: **{ADVISOR.name}**")
    with st.expander("Demo controls"):
        if st.checkbox("I understand this clears every move in the local draft plan", key="confirm_reset"):
            if st.button("Reset draft plan"):
                STORE.reset()
                ss.messages = []
                st.rerun()
    st.caption("Demo data. Source data is read-only; plan changes live in local_state/." if _running_locally() else "Demo data. Your draft plan is private to this browser session and resets when you leave.")

if not ss.messages:
    ss.messages.append({"role": "assistant", **ADVISOR.briefing(ss.ctx)})
if ss.pending_prompt:
    p_, ss.pending_prompt = ss.pending_prompt, None
    ask(p_)

P = persona()
st.markdown(f'<div class="nb-top"><p class="nb-title">FY27 Planning · {esc(DATA.name_of(P.scope_id))}</p>'
            f'<span class="nb-chip">{esc(P.label.split(" · ")[1] if " · " in P.label else P.label)}</span>'
            f'<span class="nb-chip demo">Demo data</span></div>'
            f'<p class="nb-sub">FY26 actuals Jan–{MONTHS[DATA.ytd_months]} (December not closed) · {esc(LABEL)}</p>',
            unsafe_allow_html=True)


# ------------------------------------------------------------------ workspace helpers
def trail_of(level: str, eid: str) -> list[tuple[str, str]]:
    trail, lvl, cur = [], level, eid
    while True:
        trail.append((lvl, cur))
        if (lvl == P.level and cur == P.scope_id) or lvl == "bu":
            break
        if lvl == "account":
            nxt = ("territory", accts_all().loc[accts_all()["account_id"] == cur, "territory_id"].iloc[0])
        elif lvl == "territory":
            nxt = ("area", DATA.territories.loc[DATA.territories["territory_id"] == cur, "area_id"].iloc[0])
        else:
            parent = DATA.hierarchy.loc[DATA.hierarchy["id"] == cur, "parent_id"].iloc[0]
            nxt = (entity_level(DATA, parent), parent)
        lvl, cur = nxt
    return list(reversed(trail))


def breadcrumb(level: str, eid: str) -> None:
    trail = trail_of(level, eid)
    path = " › ".join(f"<b>{esc(DATA.name_of(e))}</b>" if i == len(trail) - 1 else esc(DATA.name_of(e))
                      for i, (_, e) in enumerate(trail))
    c1, c2 = st.columns([5, 1])
    c1.markdown(f'<div class="nb-crumb">{path}</div>', unsafe_allow_html=True)
    if len(trail) > 1 and c2.button("⬆ Up a level", key=f"up_{eid}", width="stretch"):
        ss.ctx["focus"] = trail[-2]
        st.rerun()


def account_card(aid: str) -> None:
    d = account_detail(DATA, accts_all(), aid)
    if "error" in d:
        st.error(d["error"])
        return
    st.markdown(f"##### {md(d['name'])}")
    st.caption(md(f"{d['industry']} · {d['employee_band']} employees · {d['motion']} account · {d['territory_name']} "
                  f"({d['territory_motion']}) · owner {d['owner']} · {d['area']}, {d['division']}"
                  + (" · moved in draft plan" if d["moved_in_plan"] else "")))
    if d["motion"] != d["territory_motion"]:
        st.warning(f"Motion mismatch: {d['motion']} account in {a_an(d['territory_motion'])} territory.")
    kpi_tiles([("FY26 Jan–Nov", fmt_money(d["fy26_ytd"]), f"{fmt_pct(d['yoy_pct'])} YoY", tone(d["yoy_pct"])),
               ("FY25 full year", fmt_money(d["fy25_total"]), "12 months", ""),
               ("Open pipeline", fmt_money(d["open_pipeline"]), f"weighted {fmt_money(d['weighted_pipeline'])}", ""),
               ("Industry FY27 outlook", fmt_pct(d["industry_outlook_pct"]), "fictional signal", tone(d["industry_outlook_pct"]))])
    m = pd.DataFrame(d["monthly"])
    if len(m):
        chart = alt.Chart(m).mark_line(point=True).encode(
            x=alt.X("month:O", title="Month"), y=alt.Y("revenue_usd:Q", title="Revenue (USD)", axis=alt.Axis(format="$~s")),
            color=alt.Color("fiscal_year:N", title="Fiscal year", scale=alt.Scale(range=["#8e9bbd", BLUE]))
        ).properties(height=220)
        st.altair_chart(chart, width="stretch")
    if d["opportunities"]:
        st.dataframe(pd.DataFrame(d["opportunities"]), hide_index=True, width="stretch",
                     column_config={"amount_usd": st.column_config.NumberColumn("Amount", format="dollar"),
                                    "probability": st.column_config.NumberColumn("Probability", format="percent")})
    if d.get("signal"):
        st.info(f"Market signal (fictional): {d['signal']} — {d['signal_impact']}, intent score {d['intent_score']}")
    c1, c2 = st.columns(2)
    if c1.button("Plan a move for this account", key=f"plan_{aid}", width="stretch"):
        ss.selected_account = aid
        ss.goto_step = "moves"
        st.rerun()
    if c2.button("🤖 Where should it go?", key=f"askwhere_{aid}", width="stretch"):
        ask(f"Where should {d['name']} go?", from_workspace=True)


def accounts_table(df: pd.DataFrame, key: str):
    tmap = DATA.territories.set_index("territory_id")
    view = pd.DataFrame({
        "Account": df["account_name"], "ID": df["account_id"], "Motion": df["motion"],
        "Territory": df["territory_id"].map(tmap["territory_name"]), "Terr. motion": df["territory_id"].map(tmap["motion"]),
        "Owner": df["owner_name"], "FY26 Jan–Nov": df["fy26_ytd"], "YoY %": df["yoy_pct"],
        "Open pipeline": df["open_pipeline"], "Industry": df["industry"], "Signal": df["signal"], "Moved": df["moved"],
    })
    ev = st.dataframe(view, hide_index=True, width="stretch", height=380, key=key, on_select="rerun",
                      selection_mode="single-row", column_config={
                          "FY26 Jan–Nov": st.column_config.NumberColumn(format="dollar"),
                          "Open pipeline": st.column_config.NumberColumn(format="dollar"),
                          "YoY %": st.column_config.NumberColumn(format="%.1f%%")})
    return ev, view


def selected_row(event, view):
    rows = getattr(getattr(event, "selection", None), "rows", []) or []
    return view.iloc[rows[0]] if rows else None


# ------------------------------------------------------------------ workspace
step = ss.step
st.markdown(f"#### {STEP_LABEL[step]}")
A = scoped_accounts()

if step == "business":
    focus = ss.ctx.get("focus") or (P.level, P.scope_id)
    breadcrumb(*focus)
    if focus[0] == "account":
        account_card(focus[1])
    else:
        s = summarize(DATA, accts_all(), focus[0], focus[1])
        child = CHILD[focus[0]]
        kids = pd.DataFrame(s["children"])
        lines = [f"<b>{esc(DATA.name_of(focus[1]))}</b>: {esc(fmt_money(s['fy26_ytd']))} Jan–Nov, "
                 f"{esc(fmt_pct(s['yoy_pct']))} vs the same months last year."]
        acts = []
        if len(kids) and "yoy_pct" in kids and kids["yoy_pct"].notna().any():
            k2 = kids.dropna(subset=["yoy_pct"])
            lo, hi = k2.loc[k2["yoy_pct"].idxmin()], k2.loc[k2["yoy_pct"].idxmax()]
            lines.append(f"Fastest-growing {esc(child)}: <b>{esc(hi['name'])}</b> ({esc(fmt_pct(hi['yoy_pct']))}); "
                         f"weakest: <b>{esc(lo['name'])}</b> ({esc(fmt_pct(lo['yoy_pct']))}).")
            acts.append((f"Why is {lo['name']} weak?" if child != "account" else "Declining accounts here",
                         f"drill into {lo['name']}" if child != "account" else "which accounts are declining?"))
        if s["declining_accounts"]:
            lines.append(f"{s['declining_accounts']} accounts are down 15% or more YoY — candidates for a coverage review.")
            acts.append(("Show declining accounts", "which accounts are declining?"))
        if focus[0] in ("bu", "division", "area"):
            pr = purity_report(DATA, A if focus == (P.level, P.scope_id) else
                               A[A[{"bu": "bu_id", "division": "division_id", "area": "area_id"}[focus[0]]] == focus[1]],
                               scoped_territories() if focus == (P.level, P.scope_id) else
                               DATA.territories[DATA.territories[{"bu": "bu_id", "division": "division_id", "area": "area_id"}[focus[0]]] == focus[1]])
            if pr["critical"] or pr["mixed"]:
                lines.append(f"Territory purity: {pr['critical']} critical and {pr['mixed']} mixed territories need attention.")
                acts.append(("Fix purity", "are my territories pure?"))
        acts.append(("Top pipeline accounts", "top accounts by open pipeline"))
        insights(lines, acts[:4], key=f"ins_{focus[1]}")
        kpi_tiles([("FY26 Jan–Nov", fmt_money(s["fy26_ytd"]), f"{fmt_pct(s['yoy_pct'])} YoY", tone(s["yoy_pct"])),
                   ("Open pipeline", fmt_money(s["open_pipeline"]), "all stages", ""),
                   ("Accounts", f"{s['accounts']:,}", "in this view", ""),
                   ("Territories", f"{s['territories']:,}", "", ""),
                   ("Declining accounts", f"{s['declining_accounts']:,}", "≤ -15% YoY", "down" if s["declining_accounts"] else "")])
        if len(kids):
            kids["growth"] = kids["yoy_pct"].apply(lambda v: "n/a" if v is None or pd.isna(v) else ("Growing" if v >= 0 else "Declining"))
            c1, c2 = st.columns([1, 1], gap="medium")
            with c1:
                chart = alt.Chart(kids.head(15)).mark_bar(cornerRadiusEnd=3).encode(
                    x=alt.X("fy26_ytd:Q", title="FY26 Jan–Nov revenue", axis=alt.Axis(format="$~s")),
                    y=alt.Y("name:N", sort="-x", title=None),
                    color=alt.Color("growth:N", title="YoY", legend=alt.Legend(orient="bottom"),
                                    scale=alt.Scale(domain=["Growing", "Declining", "n/a"], range=[BLUE, ORANGE, "#c9d1e3"])),
                    tooltip=["name", alt.Tooltip("fy26_ytd:Q", format="$,.0f"), alt.Tooltip("yoy_pct:Q", format="+.1f")]
                ).properties(height=min(40 + 24 * min(len(kids), 15), 420))
                st.altair_chart(chart, width="stretch")
            with c2:
                st.caption(f"Select a row to drill into a {child}.")
                cols = ["name", "fy26_ytd", "yoy_pct", "open_pipeline"] + [c3 for c3 in ("motion", "owner", "accounts") if c3 in kids]
                ev = st.dataframe(kids[cols], hide_index=True, width="stretch", key=f"kids_{focus[1]}", on_select="rerun",
                                  selection_mode="single-row", height=min(40 + 35 * min(len(kids), 11), 420),
                                  column_config={"name": st.column_config.TextColumn(child.title()),
                                                 "fy26_ytd": st.column_config.NumberColumn("FY26 Jan–Nov", format="dollar"),
                                                 "open_pipeline": st.column_config.NumberColumn("Pipeline", format="dollar"),
                                                 "yoy_pct": st.column_config.NumberColumn("YoY %", format="%.1f%%")})
                rows = getattr(getattr(ev, "selection", None), "rows", []) or []
                if rows:
                    pick = kids.iloc[rows[0]]
                    ss.ctx["focus"] = (pick["level"], pick["id"])
                    ss.pop(f"kids_{focus[1]}", None)   # clear the selection so "up" doesn't re-drill
                    st.rerun()

elif step == "accounts":
    tm = A["territory_id"].map(DATA.territories.set_index("territory_id")["motion"])
    insights([f"{len(A):,} accounts in your scope; {int((A['yoy_pct'] <= -15).sum())} are down 15%+ YoY.",
              f"{int((A['motion'] != tm).sum())} accounts sit in a territory with a different sales motion.",
              f"Largest open pipeline: <b>{esc(A.sort_values('open_pipeline', ascending=False).iloc[0]['account_name'])}</b> "
              f"({esc(fmt_money(A['open_pipeline'].max()))})."],
             [("Declining accounts", "which accounts are declining?"), ("Top pipeline", "top accounts by open pipeline"),
              ("Motion mismatches", "are my territories pure?")], key="ins_acc")
    f1, f2, f3, f4 = st.columns(4)
    areas = sorted(A["area_id"].unique())
    area = f1.selectbox("Area", ["All"] + areas, format_func=lambda x: x if x == "All" else DATA.name_of(x))
    sub = A if area == "All" else A[A["area_id"] == area]
    terrs = sorted(sub["territory_id"].unique())
    terr = f2.selectbox("Territory", ["All"] + terrs, format_func=lambda x: x if x == "All" else DATA.name_of(x))
    sub = sub if terr == "All" else sub[sub["territory_id"] == terr]
    motions = f3.multiselect("Motion", ["Deep", "Expansion", "Greenfield"])
    if motions:
        sub = sub[sub["motion"].isin(motions)]
    q = f4.text_input("Search account / owner")
    if q:
        sub = sub[sub["account_name"].str.contains(q, case=False) | sub["owner_name"].str.contains(q, case=False)]
    g1, g2, g3 = st.columns(3)
    if g1.toggle("Declining only (≤ -15% YoY)"):
        sub = sub[sub["yoy_pct"] <= -15]
    if g2.toggle("Motion mismatch only"):
        sub = sub[sub["motion"] != sub["territory_id"].map(DATA.territories.set_index("territory_id")["motion"])]
    if g3.toggle("Moved in draft plan"):
        sub = sub[sub["moved"]]
    st.caption(md(f"{len(sub)} accounts · FY26 Jan–Nov {fmt_money(sub['fy26_ytd'].sum())} · open pipeline "
                  f"{fmt_money(sub['open_pipeline'].sum())} · select a row to open the account"))
    ev, view = accounts_table(sub.sort_values("fy26_ytd", ascending=False), "acct_table")
    st.download_button("Download CSV", view.to_csv(index=False), "accounts_in_scope.csv", "text/csv")
    row = selected_row(ev, view)
    if row is not None:
        ss.selected_account = row["ID"]
        ss.ctx["focus"] = ("account", row["ID"])
        account_card(row["ID"])

elif step == "moves":
    ids = list(A.sort_values("account_name")["account_id"])
    default = ids.index(ss.selected_account) if ss.selected_account in ids else 0
    names = A.set_index("account_id")["account_name"]
    c1, c2 = st.columns(2)
    aid = c1.selectbox("Account", ids, index=default, format_func=lambda x: f"{names[x]} ({x})")
    ss.selected_account = aid
    cur = A[A["account_id"] == aid].iloc[0]
    T = scoped_territories()
    allt = accts_all()
    tids = [t for t in T["territory_id"] if t != cur["territory_id"]]
    tlabel = {r.territory_id: f"{r.territory_name} · {r.motion} · {DATA.name_of(r.owner_id)} · "
                              f"{territory_load(allt, r.territory_id)['accounts']} accts" for r in T.itertuples()}
    best = suggest_targets(DATA, allt, aid, list(T["territory_id"]), DEFAULT_RULES)
    best_id = best[0]["to_territory_id"] if best else (tids[0] if tids else None)
    to = c2.selectbox("Move to territory", tids, index=tids.index(best_id) if best_id in tids else 0,
                      format_func=lambda x: tlabel[x])
    insights([f"<b>{esc(cur['account_name'])}</b> today: {esc(DATA.name_of(cur['territory_id']))} · owner "
              f"{esc(cur['owner_name'])} · {esc(cur['motion'])} account · {esc(fmt_money(cur['fy26_ytd']))} Jan–Nov · "
              f"pipeline {esc(fmt_money(cur['open_pipeline']))}.",
              (f"Best fit by the Advisor's rules: <b>{esc(best[0]['to_territory_name'])}</b> "
               f"({esc(best[0]['verdict'])}, score {best[0]['score']:+d}) — preselected below." if best else
               "No other territory in your scope.")],
             [("🤖 Ask: where should it go?", f"Where should {cur['account_name']} go?"),
              ("🤖 FY27 impact of my plan", "What will my draft plan do to FY27 revenue?")], key=f"ins_mv_{aid}")
    if to:
        ev = evaluate_move(DATA, allt, aid, to, DEFAULT_RULES)
        if "error" in ev:
            st.error(ev["error"])
        else:
            render_proposal(ev, f"ws_{aid}_{to}", expanded=True)
    st.markdown("##### Draft FY27 plan — moves in your scope")
    mv = [m for m in STORE.moves() if m["account_id"] in set(A["account_id"])]
    if not mv:
        st.caption("No moves yet.")
    for m in reversed(mv):
        c1, c2 = st.columns([6, 1])
        c1.markdown(md(f"`{m['move_id']}` **{DATA.name_of(m['account_id'])}**: {DATA.name_of(m['from_territory_id'])} → "
                       f"{DATA.name_of(m['to_territory_id'])} · *{m['advisor_verdict']}* · “{m['reason']}” · "
                       f"{m['by'].split(' · ')[0]}, {m['at'][:16].replace('T', ' ')} UTC"))
        if c2.button("Revert", key=f"rev_{m['move_id']}"):
            try:
                STORE.revert(m["move_id"], P.label)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    if mv:
        st.download_button("Export plan changes (CSV)", pd.DataFrame(mv).to_csv(index=False), "fy27_plan_moves.csv")
        pi = toolkit().call("plan_revenue_impact")
        if "error" not in pi and pi.get("items"):
            sg = lambda v: ("+" if v >= 0 else "") + fmt_money(v)
            st.markdown("##### FY27 revenue impact of your draft plan (estimate)")
            kpi_tiles([("Revenue changing hands", fmt_money(sum(i["fy27_if_move"] for i in pi["items"])), "nets out at company level", ""),
                       ("Company effect (likely)", sg(pi["company_delta"]), f"range {sg(pi['company_delta_low'])} to {sg(pi['company_delta_high'])}",
                        tone(pi["company_delta"])),
                       ("Moves", str(pi["move_count"]), "in your scope", "")])
            st.dataframe(pd.DataFrame(pi["territory_shifts"])[["territory_name", "fy27_change"]], hide_index=True,
                         width="stretch", column_config={"territory_name": st.column_config.TextColumn("Territory"),
                                                         "fy27_change": st.column_config.NumberColumn("FY27 change", format="dollar")})
            with st.expander("Assumptions behind this estimate"):
                st.markdown(md("\n".join(f"- {a}" for a in pi["assumptions"])))

elif step == "purity":
    rep = purity_report(DATA, A, scoped_territories())
    insights([f"{rep['pure']} of {len(rep['territories'])} territories are pure; {rep['critical']} critical "
              f"(Deep accounts in Greenfield territories) and {rep['mixed']} mixed.",
              f"{len(rep['misfits'])} accounts sit in a territory of another motion; fixes below are ranked by account size.",
              "Pure = every account in a territory shares its sales motion (Deep / Expansion / Greenfield)."],
             [("🤖 Summarize purity", "are my territories pure?"),
              ("🤖 FY27 impact of these fixes", "What's the FY27 revenue impact of these fixes?")], key="ins_pur")
    kpi_tiles([("Pure territories", rep["pure"], "", "up"), ("Mixed", rep["mixed"], "", "down" if rep["mixed"] else ""),
               ("Critical", rep["critical"], "Deep in Greenfield", "down" if rep["critical"] else ""),
               ("Misfit accounts", len(rep["misfits"]), "to review", "")])
    tdf = pd.DataFrame(rep["territories"])
    st.dataframe(tdf[["status", "territory_name", "motion", "owner", "accounts", "matching", "purity_pct", "misfit_motions"]],
                 hide_index=True, width="stretch", height=280,
                 column_config={"purity_pct": st.column_config.ProgressColumn("Purity", min_value=0, max_value=100, format="%.0f%%")})
    st.markdown("##### Suggested fixes — each is advice until you apply it")
    n = st.slider("Show", 1, max(1, min(15, len(rep["misfits"]))), min(5, max(1, len(rep["misfits"])))) if rep["misfits"] else 0
    for mf in rep["misfits"][:n]:
        fix = purity_fix(DATA, accts_all(), mf, DEFAULT_RULES)
        if fix and "error" not in fix:
            render_proposal(fix, f"pfix_{mf['account_id']}")
        else:
            st.caption(f"{mf['account_name']}: no {mf['account_motion']} territory in this area — consider a new "
                       f"territory or a cross-area move (Phase 2 hierarchy design).")
    st.caption(LABEL)

else:
    phase = next(ph for k, _, ph in STEPS if k == step)
    plans = {
        "hierarchy": "Define sellers by role (AE, SE, CSM, SDR, managers), link them to territories, and assign "
                     "incentive plans (OTE, base/variable split, accelerators) or non-incentive plans.",
        "revenue": "The Advisor checks current-year revenue on its own and flags discrepancies to the team "
                   "(unowned revenue, unexplained swings, feeds that disagree). It estimates December three ways "
                   "(prior-year December share, run rate, December-closing pipeline) as a low/likely/high range, "
                   "then you lock the baseline with a January true-up rule.",
        "quota": "Design the quota model: BU target → divisions → areas → territories → accounts, in proportion "
                 "to locked revenue plus growth signals. Teams can override; the Advisor suggests changes with reasons.",
        "seller": "Sellers see their allocated quota, an estimated payout at 50/100/150% attainment, and can "
                  "contest it with reasons. Disputes go to an approval queue.",
    }
    st.info(f"**Coming in Phase {phase}.** {plans[step]}")
    st.caption("Phase 1 covers steps 1–4.")


# ------------------------------------------------------------------ floating Advisor (bottom-right)
if ss.teaser and ss.messages:
    first = ss.messages[0]["text"].split("\n")[0].replace("**", "")
    st.markdown(f'<div class="nb-teaser">👋 <b>Planning Advisor</b><br>{esc(first)}<br>'
                f'<span class="m">Click 🤖 to ask me anything.</span></div>', unsafe_allow_html=True)

with st.container(key="advisor_fab"):
    try:
        pop = st.popover("🤖 Advisor", type="primary", key="advisor_pop", on_change="rerun")
        POP_STATE = True
    except TypeError:    # Streamlit versions without popover state
        pop = st.popover("🤖 Advisor")
        POP_STATE = False
    with pop:
        focus = ss.ctx.get("focus")
        st.markdown(f"**🤖 Planning Advisor** · <span style='color:{MUTED};font-size:.8rem'>{esc(ADVISOR.name)}</span>",
                    unsafe_allow_html=True)
        st.caption(md(f"Context: {DATA.name_of(focus[1]) if focus else DATA.name_of(P.scope_id)} — follow-ups apply here."))
        box = st.container(height=420, border=False)
        with st.form("advisor_form", clear_on_submit=True, border=False):
            c1, c2 = st.columns([5, 1])
            q = c1.text_input("Ask the Advisor", key="advisor_q", label_visibility="collapsed",
                              placeholder="Ask about your business, an area, an account, a move…")
            sent = c2.form_submit_button("Send", type="primary")
        if sent and q.strip():
            ask(q.strip())
            if POP_STATE:          # refresh the workspace to the Advisor's new focus; popover stays open
                st.rerun()
        with box:
            for i, m in enumerate(ss.messages):
                with st.chat_message("user" if m["role"] == "user" else "assistant",
                                     avatar="🧑" if m["role"] == "user" else "🤖"):
                    st.markdown(md(m["text"]))
                    if m["role"] != "assistant":
                        continue
                    for j, ev in enumerate(m.get("proposals", [])):
                        render_proposal(ev, f"chat_{i}_{j}", expanded=len(m.get("proposals", [])) == 1)
                    meta = []
                    g = m.get("grounding")
                    if g and g["figures_checked"]:
                        meta.append(f"{g['figures_checked']} figures checked · " + ("all traceable to data" if g["all_traceable"]
                                                                                    else "UNTRACEABLE: " + ", ".join(g["untraceable"])))
                    if m.get("engine_note"):
                        meta.append(m["engine_note"])
                    if meta:
                        st.caption(md(" · ".join(meta)))
            last = ss.messages[-1] if ss.messages else {}
            for k, s in enumerate((last.get("suggestions") or [])[:3]):
                if st.button(f"↳ {s}", key=f"sugg_{len(ss.messages)}_{k}"):
                    ss.pending_prompt = s
                    st.rerun()
