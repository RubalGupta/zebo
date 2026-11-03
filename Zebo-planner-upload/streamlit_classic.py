"""Norbae AI Annual Sales Planning Advisor — classic Streamlit front end.

Run:  streamlit run streamlit_classic.py   (classic 7-tab demo; the FY27 planning cockpit is streamlit_app.py)

This is an alternative UI over the SAME tested modules in src/ (analysis tools,
allowlisted agent layer, no-LLM intent router, review store, evaluation lab).
It contains no business logic of its own and never writes to data/.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from agent_blueprint import (PROHIBITED_ACTIONS, create_change_request,  # noqa: E402
                             evidence_for_territory, tool_catalog)
from analysis_tools import (DataValidationError, check_data_consistency,  # noqa: E402
                            evaluate_all_territories, get_territory_detail, money, pct,
                            preview_quota_scenario, signed_money, source_fingerprint,
                            summarize_plan, validate_all_data)
from case_study import build_case_study  # noqa: E402
from config import DEFAULT_THRESHOLDS, MAX_TOOL_CALLS_PER_QUESTION, THRESHOLD_LABEL, Thresholds  # noqa: E402
from evaluation import MANUAL, run_evaluations  # noqa: E402
from intent_router import EXAMPLE_QUESTIONS, FALLBACK_TEXT, MODE_LABEL, answer_question  # noqa: E402
from review_store import PENDING, ReviewStore  # noqa: E402

st.set_page_config(page_title="Sales Planning Advisor", page_icon="📊", layout="wide")

ACCENT = "#1d4f5e"
st.markdown(
    f"""<style>
    .block-container {{padding-top: 1.6rem; max-width: 1300px;}}
    .nb-badge {{display:inline-block;font-size:.78rem;font-weight:600;padding:.2rem .6rem;border-radius:999px;margin-right:.4rem}}
    .nb-demo {{background:#fff3dc;color:#8a5300;border:1px solid #f0cf8f}}
    .nb-mode {{background:#eef2f7;color:#17202b;border:1px solid #d9dee6}}
    .nb-brand {{font-weight:700;letter-spacing:.06em;color:{ACCENT}}}
    </style>""",
    unsafe_allow_html=True,
)

# ------------------------------------------------------------------ session state
store = ReviewStore()
if "thresholds" not in st.session_state:
    st.session_state.thresholds = DEFAULT_THRESHOLDS.to_dict()
if "conversation" not in st.session_state:
    st.session_state.conversation = []          # list of advisor responses
if "unsaved_drafts" not in st.session_state:
    st.session_state.unsaved_drafts = {}        # request_id -> draft from the advisor
if "startup_fingerprint" not in st.session_state:
    st.session_state.startup_fingerprint = source_fingerprint()


def thresholds() -> Thresholds:
    return Thresholds.from_mapping(st.session_state.thresholds)


def integrity_box() -> None:
    unchanged = source_fingerprint() == st.session_state.startup_fingerprint
    if unchanged:
        st.success("✓ Source CSVs unchanged since this session started (SHA-256 re-checked). "
                   "Draft requests and review decisions never modify planning data.")
    else:
        st.error("Source CSVs changed since this session started (edited outside the app). Reload to re-baseline.")


# ------------------------------------------------------------------ header
st.markdown(
    '<span class="nb-brand">NORBAE AI</span> · Annual Sales Planning Advisor<br>'
    '<span class="nb-badge nb-demo">Synthetic demo data</span>'
    '<span class="nb-badge nb-mode">No-LLM baseline · no model connected</span>',
    unsafe_allow_html=True,
)

try:
    t = thresholds()
    summary = summarize_plan()
    territories = evaluate_all_territories(thresholds=t)["territories"]
except DataValidationError as exc:
    st.error(f"Data validation error: {exc}")
    st.stop()

tabs = st.tabs(["Overview", "Planning Exceptions", "Ask the Advisor", "Scenario Review",
                "Evaluation Lab", "Architecture & Governance", "Case Study"])

# ------------------------------------------------------------------ A. Overview
with tabs[0]:
    st.header("Plan overview")
    st.caption("Calculated from the project CSV files on every run — nothing is hard-coded.")
    flagged = [x for x in territories if x["flags"]]
    if summary["within_target"]:
        st.success("Proposed quotas match the company target.")
    else:
        st.warning(f"**Proposed quota total differs from the company target.** {summary['interpretation']} "
                   "Exceptions are review prompts, not conclusions.")
    c = st.columns(5)
    c[0].metric("Company target", money(summary["company_target_usd"]))
    c[1].metric("Proposed quota total", money(summary["proposed_quota_total_usd"]))
    c[2].metric("Quota minus target", signed_money(summary["quota_minus_target_usd"]),
                pct(summary["quota_minus_target_pct"]) + " vs target", delta_color="off")
    c[3].metric("Territories", summary["territory_count"])
    c[4].metric("Planning exceptions", len(flagged), ", ".join(x["territory_id"] for x in flagged) or "none",
                delta_color="off")

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Variance by territory (%)")
        chart_df = pd.DataFrame({"Territory": [x["territory_id"] for x in territories],
                                 "Variance %": [x["variance_pct"] or 0 for x in territories]}).set_index("Territory")
        st.bar_chart(chart_df, color=ACCENT)
        st.caption(f"Review threshold: ±{t.variance_pct:g}%. {THRESHOLD_LABEL}")
    with right:
        st.subheader("Data status")
        v = validate_all_data()
        st.dataframe(pd.DataFrame([{"File": f, "Status": "Valid" if r["ok"] else "Error",
                                    "Rows / error": r.get("rows", r.get("error"))} for f, r in v["files"].items()]),
                     hide_index=True, width="stretch")
        integrity_box()

# ------------------------------------------------------------------ B. Exceptions
with tabs[1]:
    st.header("Planning exceptions")
    st.caption("A flag means *review this input*, not *this quota is wrong*.")
    with st.expander("Illustrative demo thresholds — not industry standards; require business-owner validation"):
        with st.form("thresholds_form"):
            cols = st.columns(5)
            vals = {
                "variance_pct": cols[0].number_input("Variance ≥ (%)", 0.1, 100.0, float(t.variance_pct), 0.1),
                "quota_to_hist_per_rep_ratio": cols[1].number_input("Quota/rep ÷ hist/rep ≥ (×)", 0.05, 100.0,
                                                                    float(t.quota_to_hist_per_rep_ratio), 0.05),
                "opportunity_index_min": cols[2].number_input("Opp. index min", 0.0, 100.0, float(t.opportunity_index_min), 0.05),
                "opportunity_index_max": cols[3].number_input("Opp. index max", 0.0, 100.0, float(t.opportunity_index_max), 0.05),
                "reconciliation_pct": cols[4].number_input("Roster note ≥ (%)", 0.0, 100.0, float(t.reconciliation_pct), 0.1),
            }
            a, b = st.columns([1, 5])
            apply = a.form_submit_button("Apply")
            reset = b.form_submit_button("Reset to defaults")
        if apply:
            try:
                Thresholds.from_mapping(vals)
                st.session_state.thresholds = vals
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        if reset:
            st.session_state.thresholds = DEFAULT_THRESHOLDS.to_dict()
            st.rerun()

    f1, f2, f3, f4 = st.columns([1, 1, 1, 2])
    scope = f1.selectbox("Show", ["Flagged only", "All territories"])
    region = f2.selectbox("Region", ["All"] + sorted({x["region"] for x in territories}))
    rule = f3.selectbox("Rule", ["Any"] + sorted({r["rule_id"] for x in territories for r in x["rules"]}))
    search = f4.text_input("Search", placeholder="Territory ID or name")
    rows = [x for x in territories
            if (scope == "All territories" or x["flags"]) and (region == "All" or x["region"] == region)
            and (rule == "Any" or rule in x["flags"])
            and (not search or search.lower() in (x["territory_id"] + " " + x["territory_name"]).lower())]
    if rows:
        st.dataframe(pd.DataFrame([{
            "Territory": f"{x['territory_id']} — {x['territory_name']}",
            "Target": x["company_target_usd"], "Proposed quota": x["proposed_quota_usd"],
            "Variance $": x["variance_usd"], "Variance %": x["variance_pct"],
            "Rep capacity": x["rep_capacity"], "Quota / rep": x["quota_per_rep_usd"],
            "Hist. rev / rep": x["historical_revenue_per_rep_usd"], "Opp. index": x["opportunity_index"],
            "Reason(s) flagged": " ".join(x["reasons"]) or "—",
            "Review": "Review required" if x["review_required"] else "No rule triggered",
        } for x in rows]), hide_index=True, width="stretch", column_config={
            "Target": st.column_config.NumberColumn(format="dollar"),
            "Proposed quota": st.column_config.NumberColumn(format="dollar"),
            "Variance $": st.column_config.NumberColumn(format="dollar"),
            "Variance %": st.column_config.NumberColumn(format="%.2f%%"),
            "Quota / rep": st.column_config.NumberColumn(format="dollar"),
            "Hist. rev / rep": st.column_config.NumberColumn(format="dollar"),
        })
    else:
        st.info("No territories match these filters.")

    st.subheader("Territory detail")
    ids = [x["territory_id"] for x in territories]
    default = next((i for i, x in enumerate(territories) if x["flags"]), 0)
    tid = st.selectbox("Territory", ids, index=default,
                       format_func=lambda i: next(f"{x['territory_id']} — {x['territory_name']}" for x in territories
                                                  if x["territory_id"] == i))
    d = get_territory_detail(tid, thresholds=t)
    if "error" in d:
        st.error(d["error"])
    else:
        (st.warning if d["rule_triggered"] else st.info)(d["interpretation"])
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Calculation logic**")
            st.markdown("\n".join(f"{i}. {s}" for i, s in enumerate(d["calculation_steps"], 1)))
            st.markdown("**Source data**")
            for f, row in d["source_rows"].items():
                st.caption(f)
                st.json(row, expanded=False)
        with c2:
            st.markdown("**Rule evaluation** (illustrative)")
            st.dataframe(pd.DataFrame([{"Rule": r["label"], "Threshold": r["threshold"], "Observed": r["observed"],
                                        "Triggered": "Yes" if r["triggered"] else "No"} for r in d["rules"]]),
                         hide_index=True, width="stretch")
            ro, p = d["roster_summary"], d["pipeline_sample"]
            if ro:
                st.markdown(f"**Roster (aggregate only):** {ro['active_reps']} active reps / capacity "
                            f"{ro['planned_capacity']:g}; roster revenue {money(ro['roster_historical_revenue_usd'])} vs "
                            f"territory history {money(ro['territory_historical_revenue_usd'])} ({pct(ro['difference_pct'])}).")
            if p:
                st.markdown(f"**Pipeline sample:** {p['account_count']} accounts, {money(p['total_opportunity_usd'])}. "
                            f"_{p['caveat']}_")
        for n in d["data_notes"]:
            st.caption(f"Data note: {n}")
        st.caption(d["caveat"])

    st.subheader("Cross-file data checks (informational)")
    cons = check_data_consistency(thresholds=t)
    if cons["findings"]:
        st.dataframe(pd.DataFrame(cons["findings"]), hide_index=True, width="stretch")
    else:
        st.caption("No cross-file inconsistencies found.")

# ------------------------------------------------------------------ C. Advisor
with tabs[2]:
    st.header("Ask the Planning Advisor")
    st.caption(f"{MODE_LABEL} {FALLBACK_TEXT} Max {MAX_TOOL_CALLS_PER_QUESTION} tool calls per question; "
               "every $ and % figure is checked against tool output.")
    chosen = None
    ex_cols = st.columns(len(EXAMPLE_QUESTIONS))
    for i, q in enumerate(EXAMPLE_QUESTIONS):
        if ex_cols[i].button(q, key=f"ex{i}", width="stretch"):
            chosen = q
    with st.form("ask_form", clear_on_submit=True):
        typed = st.text_area("Your question", max_chars=2000, height=80,
                             placeholder="e.g. Show me the details for territory T05")
        if st.form_submit_button("Ask", type="primary"):
            chosen = typed
    if chosen and chosen.strip():
        r = answer_question(chosen, thresholds=t, store=store)
        r["question"] = chosen
        if r.get("draft") and r["draft"].get("status") == PENDING:
            st.session_state.unsaved_drafts[r["draft"]["request_id"]] = r["draft"]
        st.session_state.conversation.insert(0, r)

    for k, r in enumerate(st.session_state.conversation):
        with st.chat_message("user"):
            st.write(r["question"])
        with st.chat_message("assistant"):
            g = r["grounding"]
            badge = ("no figures to check" if g["figures_checked"] == 0 else
                     f"{g['figures_checked']} figures checked · all traceable" if g["all_traceable"] else
                     f"UNTRACEABLE: {', '.join(g['untraceable'])}")
            st.caption(f"No-LLM baseline · intent: {r['intent'].replace('_', ' ')} · {badge}")
            st.markdown(r["answer"])
            draft = r.get("draft")
            if draft and draft["request_id"] in st.session_state.unsaved_drafts:
                if st.button(f"Save {draft['request_id']} to review queue", key=f"save{k}"):
                    store.save_request(st.session_state.unsaved_drafts.pop(draft["request_id"]))
                    st.success("Saved to the local demo queue — still pending human approval, not applied.")
            if r["tool_calls"]:
                with st.expander(f"Tool trace ({len(r['tool_calls'])} call(s))"):
                    st.dataframe(pd.DataFrame(r["tool_calls"]).astype(str), hide_index=True, width="stretch")

# ------------------------------------------------------------------ D. Scenario review
with tabs[3]:
    st.header("Scenario review & human approval")
    st.caption("Draft-only change requests. Demo-only workflow simulation: nothing here changes the quota plan, "
               "territories, CRM, or any system.")
    integrity_box()
    left, right = st.columns([1, 1.15])
    with left:
        st.subheader("New draft change request")
        ct = st.selectbox("Territory", ids, key="cr_tid")
        cd = get_territory_detail(ct, thresholds=t)
        st.markdown(f"Current proposed quota **{money(cd['proposed_quota_usd'])}** · target "
                    f"{money(cd['company_target_usd'])} · variance {signed_money(cd['variance_usd'])} "
                    f"({pct(cd['variance_pct'])})")
        alt = st.number_input("Suggested alternative quota (optional, USD; 0 = none)", min_value=0, step=10_000, value=0)
        if alt and st.button("Preview what-if (not applied)"):
            pv = preview_quota_scenario(ct, float(alt), thresholds=t)
            if "error" in pv:
                st.error(pv["error"])
            else:
                st.info(f"**Hypothetical — not applied.** Territory change {signed_money(pv['territory_change_usd'])}; "
                        f"variance {pct(pv['alternative_variance_pct'])}; plan total "
                        f"{money(pv['plan_total_current_usd'])} → {money(pv['plan_total_under_alternative_usd'])}; "
                        f"gap {signed_money(pv['plan_gap_current_usd'])} → {signed_money(pv['plan_gap_under_alternative_usd'])}.")
        st.markdown("**Data evidence (from tool output)**")
        evidence = evidence_for_territory(ct, thresholds=t)
        st.markdown("\n".join(f"- {e}" for e in evidence))
        with st.form("cr_form", clear_on_submit=True):
            rationale = st.text_area("Reason / business rationale (required)", max_chars=1000)
            assumptions = st.text_area("Assumptions (one per line)", max_chars=1000)
            extra = st.text_area("Additional evidence notes (optional, one per line)", max_chars=1000)
            reviewer = st.text_input("Reviewer name / role (optional)", max_chars=80)
            if st.form_submit_button("Create draft (PENDING HUMAN APPROVAL)", type="primary"):
                if not rationale.strip():
                    st.error("A business rationale is required.")
                else:
                    draft = create_change_request(
                        "propose_quota_review", territory_id=ct, suggested_quota_usd=alt or None,
                        rationale=rationale, assumptions=assumptions, reviewer=reviewer,
                        evidence=evidence + [f"Planner note: {e.strip()}" for e in extra.splitlines() if e.strip()])
                    if draft["status"] == PENDING:
                        store.save_request(draft)
                        st.success(f"Draft {draft['request_id']} created — pending human approval, not applied.")
                    else:
                        st.error("; ".join(draft.get("errors", ["Invalid draft"])))
    with right:
        reqs = list(reversed(store.list_requests()))
        st.subheader(f"Local demo review queue ({len(reqs)})")
        if not reqs:
            st.caption("No draft requests yet.")
        for q in reqs:
            with st.container(border=True):
                st.markdown(f"**{q['territory_id']}** · `{q['request_id']}` · **{q['status'].replace('_', ' ')}**")
                st.caption(f"Not applied · executed: {q['executed']} · applied to source data: "
                           f"{q['applied_to_source_data']} · simulation")
                st.markdown(f"Current {money(q['current_proposed_quota_usd'])} · suggested "
                            f"{money(q['suggested_quota_usd']) if q['suggested_quota_usd'] else 'none'} · "
                            f"created {q['created_at']}")
                st.markdown(f"**Rationale:** {q['rationale']}")
                for h in q.get("review_history", []):
                    st.caption(f"{h['timestamp']} · {h['action'].replace('_', ' ')} by {h['reviewer']} — "
                               f"“{h['note']}” (demo record only)")
                if q["status"] == PENDING:
                    note = st.text_input("Audit note (required)", key=f"note_{q['request_id']}")
                    who = st.text_input("Reviewer", key=f"who_{q['request_id']}")
                    b1, b2 = st.columns(2)
                    for col, action, label in ((b1, "mark_reviewed", "Mark reviewed"),
                                               (b2, "return_for_revision", "Return for revision")):
                        if col.button(label, key=f"{action}_{q['request_id']}"):
                            try:
                                store.review_request(q["request_id"], action, note, who)
                                st.rerun()
                            except ValueError as exc:
                                st.error(str(exc))

# ------------------------------------------------------------------ E. Evaluation lab
with tabs[4]:
    st.header("Evaluation lab")
    st.caption("A case shows as passed only if its automated check actually ran and passed. Model-response cases "
               "stay “Manual evaluation required” until a human records a result.")
    b1, b2 = st.columns([1, 1])
    run = None
    if b1.button("Run automated checks", type="primary"):
        run = run_evaluations(store=store)
    if b2.button("Run unit test suite"):
        with st.spinner("Running tests…"):
            proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=ROOT,
                                  capture_output=True, text=True, timeout=300,
                                  env={**__import__("os").environ, "NORBAE_SKIP_SERVER_SELFTEST": "1"})
        out = (proc.stderr + proc.stdout).strip().splitlines()
        (st.success if proc.returncode == 0 else st.error)(" ".join(out[-3:]))
    run = run or store.last_eval_run()
    if run:
        s = run["summary"]
        m = st.columns(4)
        m[0].metric("Automated passed", f"{s['automated_passed']} / {s['automated_total']}")
        m[1].metric("Automated failed", s["automated_failed"])
        m[2].metric("Manual pending", s["manual_pending"])
        m[3].metric("Manual recorded", s["manual_recorded"])
        st.caption(f"Last run: {s['run_at']} UTC")
        st.dataframe(pd.DataFrame([{k: r.get(k) for k in ("test_id", "category", "test", "expected_result", "actual",
                                                           "status", "severity", "ran_at")} for r in run["results"]]),
                     hide_index=True, width="stretch")
        manual_ids = [r["test_id"] for r in run["results"] if r["evaluation_type"] == "manual"]
        with st.expander("Record a manual evaluation result"):
            with st.form("manual_eval"):
                tc = st.selectbox("Case", manual_ids)
                outcome = st.radio("Outcome", ["Pass", "Fail"], horizontal=True)
                who = st.text_input("Reviewer (required)")
                notes = st.text_area("Notes: what was tested, which model/prompt, evidence")
                if st.form_submit_button("Save manual result"):
                    try:
                        store.record_manual_evaluation(tc, outcome, who, notes)
                        st.success("Recorded. Re-run automated checks to refresh the table.")
                    except ValueError as exc:
                        st.error(str(exc))
    else:
        st.info(f"Not run yet. Select “Run automated checks”. Manual cases will show “{MANUAL}”.")

# ------------------------------------------------------------------ F. Architecture
with tabs[5]:
    st.header("Architecture & governance")
    st.markdown("**Workflow:** user question → intent router / optional model → allowlisted read-only tool → "
                "deterministic calculation → evidence-grounded response → human review → draft-only change request")
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Boundaries")
        st.markdown("\n".join(f"- {b}" for b in [
            "Synthetic data only.",
            "Read-only analysis; no writes to business systems, CRM, email, or external services.",
            "Human approval required for consequential decisions; drafts are never applied.",
            "Numeric claims must be traceable to data/tool results (automated grounding check).",
            "No employee ranking, termination, or individual performance decisions; rep data aggregate only.",
            "No unsupported claims about savings, accuracy, or production readiness.",
            "Tool calls and review decisions logged locally without questions, secrets, or personal data.",
        ]))
    with c2:
        st.subheader("Prohibited actions (enforced in code)")
        st.markdown("\n".join(f"- ✕ {p}" for p in PROHIBITED_ACTIONS))
    st.subheader("Tool allowlist")
    st.dataframe(pd.DataFrame([{"Tool": x["name"], "Kind": x["kind"], "Purpose": x["purpose"],
                                "Arguments": ", ".join(x["args"]) or "none"} for x in tool_catalog()]),
                 hide_index=True, width="stretch")
    st.subheader("Local demo audit log")
    audit = store.read_audit(60)
    if audit:
        st.dataframe(pd.DataFrame(audit).astype(str), hide_index=True, width="stretch")
    else:
        st.caption("No audit entries yet.")

# ------------------------------------------------------------------ G. Case study
with tabs[6]:
    md = build_case_study(store)
    st.download_button("Download Markdown", md, file_name="norbae_case_study.md", mime="text/markdown")
    st.markdown(md)
