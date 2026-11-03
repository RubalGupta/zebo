"""Deterministic tools for the Norbae AI Annual Sales Planning Advisor.

Uses only the Python standard library. Every number shown in the app or in an
advisor answer is calculated here from the project CSVs at runtime.

Design rules
- All functions are READ-ONLY. Nothing in this module writes to any file.
- Data problems raise ``DataValidationError`` with a message that names the
  project file and row/column, never an absolute filesystem path.
- Unknown territory IDs fail closed: an error dict is returned and no
  territory data is invented.
- Thresholds are illustrative demo thresholds (see ``config.Thresholds``).
  A flag means "review this input", not "this quota is wrong".
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import (  # noqa: E402
    DEFAULT_THRESHOLDS,
    FLOAT_TOLERANCE,
    THRESHOLD_LABEL,
    Thresholds,
    data_dir as _default_data_dir,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"  # kept for backwards compatibility with the starter kit

REQUIRED_COLUMNS: dict[str, list[str]] = {
    "quota_plan.csv": [
        "territory_id", "company_target_usd", "proposed_quota_usd",
        "rep_capacity", "historical_revenue_usd", "opportunity_index",
    ],
    "territories.csv": [
        "territory_id", "region", "territory_name", "rep_capacity",
        "historical_revenue_usd", "opportunity_index",
    ],
    "sales_reps.csv": ["rep_id", "territory_id", "rep_label", "status", "historical_revenue_usd"],
    "accounts.csv": [
        "account_id", "territory_id", "account_label", "pipeline_stage",
        "opportunity_amount_usd", "confidence_band", "data_origin",
    ],
}
NUMERIC_COLUMNS: dict[str, list[str]] = {
    "quota_plan.csv": [
        "company_target_usd", "proposed_quota_usd", "rep_capacity",
        "historical_revenue_usd", "opportunity_index",
    ],
    "territories.csv": ["rep_capacity", "historical_revenue_usd", "opportunity_index"],
    "sales_reps.csv": ["historical_revenue_usd"],
    "accounts.csv": ["opportunity_amount_usd"],
}

TERRITORY_ID_PATTERN = re.compile(r"^[A-Z0-9_-]{1,12}$")

RULE_LABELS = {
    "quota_variance": "Quota vs. target variance at or above threshold",
    "quota_per_rep_vs_historical": "Quota per rep at or above threshold multiple of historical revenue per rep",
    "invalid_capacity": "Rep capacity is zero or negative",
    "invalid_target": "Company target is zero or negative",
    "opportunity_index_out_of_range": "Opportunity index outside illustrative range",
}


class DataValidationError(ValueError):
    """Raised when a project CSV is missing, empty, or malformed."""


# --------------------------------------------------------------------------- formatting

def money(value: float | None) -> str:
    """Format USD consistently: $1,175,000 / -$270,000. None -> 'n/a'."""
    if value is None:
        return "n/a"
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.0f}"


def signed_money(value: float | None) -> str:
    if value is None:
        return "n/a"
    return ("+" if value > 0 else "") + money(value)


def pct(value: float | None, signed: bool = True) -> str:
    """Format a value already expressed in percent units: 8.0 -> '+8.00%'."""
    if value is None:
        return "n/a"
    return f"{value:+.2f}%" if signed else f"{value:.2f}%"


# --------------------------------------------------------------------------- loading

def _resolve_dir(data_dir: Path | str | None) -> Path:
    return Path(data_dir) if data_dir is not None else _default_data_dir()


def load_csv(name: str, data_dir: Path | str | None = None) -> list[dict[str, str]]:
    """Load and validate a project CSV. Raises DataValidationError on problems."""
    if name not in REQUIRED_COLUMNS:
        raise DataValidationError(f"'{name}' is not a recognised project data file")
    path = _resolve_dir(data_dir) / name
    if not path.is_file():
        raise DataValidationError(f"Required data file 'data/{name}' was not found")
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames
            if not header:
                raise DataValidationError(f"'data/{name}' is empty (no header row)")
            header = [h.strip() for h in header]
            missing = [c for c in REQUIRED_COLUMNS[name] if c not in header]
            if missing:
                raise DataValidationError(f"'data/{name}' is missing required column(s): {', '.join(missing)}")
            rows = []
            for raw in reader:
                if raw is None or all((v or "").strip() == "" for v in raw.values() if isinstance(v, str)):
                    continue  # skip blank lines
                rows.append({(k or "").strip(): (v.strip() if isinstance(v, str) else v) for k, v in raw.items()})
    except UnicodeDecodeError:
        raise DataValidationError(f"'data/{name}' is not valid UTF-8 text")
    except csv.Error as exc:
        raise DataValidationError(f"'data/{name}' could not be parsed as CSV: {exc}")
    if not rows:
        raise DataValidationError(f"'data/{name}' contains no data rows")
    for i, row in enumerate(rows, start=2):  # row 1 is the header
        for col in NUMERIC_COLUMNS[name]:
            row[col] = _parse_number(row.get(col), name, i, col)
        if "territory_id" in row:
            if not row["territory_id"]:
                raise DataValidationError(f"'data/{name}' row {i}: territory_id is blank")
            row["territory_id"] = row["territory_id"].upper()
    return rows


def _parse_number(raw: Any, name: str, row_no: int, col: str) -> float:
    if raw is None or str(raw).strip() == "":
        raise DataValidationError(f"'data/{name}' row {row_no}: '{col}' is blank")
    text = str(raw).strip().replace(",", "").replace("$", "")
    try:
        value = float(text)
    except ValueError:
        raise DataValidationError(f"'data/{name}' row {row_no}: '{col}' value '{raw}' is not numeric")
    if math.isnan(value) or math.isinf(value):
        raise DataValidationError(f"'data/{name}' row {row_no}: '{col}' must be a finite number")
    return value


def load_quota_plan(data_dir: Path | str | None = None) -> list[dict[str, Any]]:
    rows = load_csv("quota_plan.csv", data_dir)
    seen: set[str] = set()
    for i, r in enumerate(rows, start=2):
        tid = r["territory_id"]
        if not TERRITORY_ID_PATTERN.match(tid):
            raise DataValidationError(f"'data/quota_plan.csv' row {i}: territory_id '{tid}' has an invalid format")
        if tid in seen:
            raise DataValidationError(f"'data/quota_plan.csv' row {i}: duplicate territory_id '{tid}'")
        seen.add(tid)
        if r["company_target_usd"] < 0 or r["proposed_quota_usd"] < 0 or r["historical_revenue_usd"] < 0:
            raise DataValidationError(f"'data/quota_plan.csv' row {i}: dollar amounts cannot be negative")
    return rows


def normalize_territory_id(territory_id: Any) -> str:
    """Validate a territory ID argument. Raises ValueError for unsafe/malformed input."""
    if not isinstance(territory_id, str):
        raise ValueError("territory_id must be a string")
    tid = territory_id.strip().upper()
    if not TERRITORY_ID_PATTERN.match(tid):
        raise ValueError("territory_id must be 1-12 characters: letters, digits, '-' or '_'")
    return tid


# --------------------------------------------------------------------------- core evaluation

def _ge(value: float, threshold: float) -> bool:
    return value >= threshold - FLOAT_TOLERANCE


def evaluate_territory(row: dict[str, Any], thresholds: Thresholds = DEFAULT_THRESHOLDS) -> dict[str, Any]:
    """Compute metrics and evaluate every illustrative rule for one quota_plan row."""
    target = row["company_target_usd"]
    quota = row["proposed_quota_usd"]
    capacity = row["rep_capacity"]
    hist = row["historical_revenue_usd"]
    opp = row["opportunity_index"]

    variance = quota - target
    variance_pct = (variance / target * 100) if target > 0 else None
    capacity_valid = capacity > 0
    quota_per_rep = quota / capacity if capacity_valid else None
    hist_per_rep = hist / capacity if capacity_valid else None
    ratio = (quota_per_rep / hist_per_rep) if (quota_per_rep is not None and hist_per_rep) else None

    rules: list[dict[str, Any]] = []

    def rule(rule_id: str, threshold_text: str, observed: str, triggered: bool, note: str = "") -> None:
        rules.append({
            "rule_id": rule_id, "label": RULE_LABELS[rule_id], "threshold": threshold_text,
            "observed": observed, "triggered": bool(triggered), "note": note,
        })

    rule("invalid_target", "target > $0", money(target), target <= 0,
         "Variance % cannot be calculated without a positive target." if target <= 0 else "")
    rule("quota_variance", f"|variance| >= {thresholds.variance_pct:g}%", pct(variance_pct),
         variance_pct is not None and _ge(abs(variance_pct), thresholds.variance_pct))
    rule("invalid_capacity", "rep capacity > 0", f"{capacity:g}", not capacity_valid,
         "Per-rep metrics are not calculated when capacity is not positive." if not capacity_valid else "")
    rule("quota_per_rep_vs_historical", f"ratio >= {thresholds.quota_to_hist_per_rep_ratio:g}x",
         f"{ratio:.3f}x" if ratio is not None else "not computable",
         ratio is not None and _ge(ratio, thresholds.quota_to_hist_per_rep_ratio),
         "" if ratio is not None else "Requires positive capacity and historical revenue.")
    out_of_range = opp < thresholds.opportunity_index_min - FLOAT_TOLERANCE or opp > thresholds.opportunity_index_max + FLOAT_TOLERANCE
    rule("opportunity_index_out_of_range",
         f"{thresholds.opportunity_index_min:g} to {thresholds.opportunity_index_max:g} (inclusive)",
         f"{opp:g}", out_of_range)

    flags = [r["rule_id"] for r in rules if r["triggered"]]
    reasons = [_reason_text(r["rule_id"], variance_pct, ratio, opp, capacity, target, thresholds) for r in rules if r["triggered"]]
    return {
        "territory_id": row["territory_id"],
        "company_target_usd": round(target, 2),
        "proposed_quota_usd": round(quota, 2),
        "variance_usd": round(variance, 2),
        "variance_pct": round(variance_pct, 2) if variance_pct is not None else None,
        "rep_capacity": capacity,
        "quota_per_rep_usd": round(quota_per_rep, 2) if quota_per_rep is not None else None,
        "historical_revenue_usd": round(hist, 2),
        "historical_revenue_per_rep_usd": round(hist_per_rep, 2) if hist_per_rep is not None else None,
        "quota_to_hist_per_rep_ratio": round(ratio, 4) if ratio is not None else None,
        "opportunity_index": opp,
        "flags": flags,
        "reasons": reasons,
        "rules": rules,
        "review_required": bool(flags),
    }


def _reason_text(rule_id, variance_pct, ratio, opp, capacity, target, t: Thresholds) -> str:
    if rule_id == "quota_variance":
        direction = "above" if variance_pct > 0 else "below"
        return f"Proposed quota is {abs(variance_pct):.2f}% {direction} target (threshold {t.variance_pct:g}%)."
    if rule_id == "quota_per_rep_vs_historical":
        return f"Quota per rep is {ratio:.2f}x historical revenue per rep (threshold {t.quota_to_hist_per_rep_ratio:g}x)."
    if rule_id == "invalid_capacity":
        return f"Rep capacity is {capacity:g}; capacity must be positive."
    if rule_id == "invalid_target":
        return f"Company target is {money(target)}; target must be positive."
    if rule_id == "opportunity_index_out_of_range":
        return (f"Opportunity index {opp:g} is outside the illustrative range "
                f"{t.opportunity_index_min:g}–{t.opportunity_index_max:g}.")
    return rule_id


def _priority_key(e: dict[str, Any]) -> tuple:
    return (-len(e["flags"]), -abs(e["variance_usd"]), e["territory_id"])


# --------------------------------------------------------------------------- public tools

def summarize_plan(data_dir: Path | str | None = None) -> dict[str, Any]:
    rows = load_quota_plan(data_dir)
    target = sum(r["company_target_usd"] for r in rows)
    quota = sum(r["proposed_quota_usd"] for r in rows)
    gap = quota - target
    gap_pct = (gap / target * 100) if target > 0 else None
    within = abs(gap) < 0.01
    if within:
        interpretation = "Proposed quota total matches company target."
    else:
        direction = "above" if gap > 0 else "below"
        interpretation = (f"Proposed quota total is {money(abs(gap))} {direction} the company target; "
                          "planner review required.")
    return {
        "tool": "summarize_plan",
        "territory_count": len(rows),
        "company_target_usd": round(target, 2),
        "proposed_quota_total_usd": round(quota, 2),
        "quota_minus_target_usd": round(gap, 2),
        "quota_minus_target_pct": round(gap_pct, 4) if gap_pct is not None else None,
        "within_target": within,
        "interpretation": interpretation,
        "source": "data/quota_plan.csv",
    }


def find_planning_exceptions(data_dir: Path | str | None = None,
                             thresholds: Thresholds = DEFAULT_THRESHOLDS) -> dict[str, Any]:
    """Return territories that meet at least one illustrative review rule, highest priority first.

    Priority order is a deterministic sort (number of rules triggered, then absolute dollar
    variance). It is a review queue order, not a judgement that a quota is wrong.
    """
    rows = load_quota_plan(data_dir)
    exceptions = [e for e in (evaluate_territory(r, thresholds) for r in rows) if e["flags"]]
    exceptions.sort(key=_priority_key)
    return {
        "tool": "find_planning_exceptions",
        "exception_count": len(exceptions),
        "territories_evaluated": len(rows),
        "thresholds": thresholds.to_dict(),
        "threshold_label": THRESHOLD_LABEL,
        "priority_rule": "Sorted by number of rules triggered, then absolute dollar variance.",
        "exceptions": exceptions,
    }


def evaluate_all_territories(data_dir: Path | str | None = None,
                             thresholds: Thresholds = DEFAULT_THRESHOLDS) -> dict[str, Any]:
    """All territories (flagged or not) joined with territory metadata, for the dashboard table."""
    rows = load_quota_plan(data_dir)
    meta = _territory_meta(data_dir)
    out = []
    for r in rows:
        e = evaluate_territory(r, thresholds)
        m = meta.get(e["territory_id"], {})
        e["region"] = m.get("region", "Unknown")
        e["territory_name"] = m.get("territory_name", "Unknown")
        out.append(e)
    return {"tool": "evaluate_all_territories", "territories": out, "thresholds": thresholds.to_dict(),
            "threshold_label": THRESHOLD_LABEL}


def _territory_meta(data_dir) -> dict[str, dict[str, Any]]:
    try:
        return {r["territory_id"]: r for r in load_csv("territories.csv", data_dir)}
    except DataValidationError:
        return {}


def get_territory_detail(territory_id: str, data_dir: Path | str | None = None,
                         thresholds: Thresholds = DEFAULT_THRESHOLDS) -> dict[str, Any]:
    try:
        tid = normalize_territory_id(territory_id)
    except ValueError as exc:
        return {"tool": "get_territory_detail", "error": f"Invalid territory_id: {exc}"}
    rows = load_quota_plan(data_dir)
    valid_ids = [r["territory_id"] for r in rows]
    row = next((r for r in rows if r["territory_id"] == tid), None)
    if row is None:
        return {"tool": "get_territory_detail", "error": f"Unknown territory_id: {tid}", "valid_ids": valid_ids}

    e = evaluate_territory(row, thresholds)
    detail: dict[str, Any] = {"tool": "get_territory_detail", **e}
    detail["source_rows"] = {"data/quota_plan.csv": _display_row(row)}
    notes: list[str] = []

    meta = _territory_meta(data_dir).get(tid)
    if meta:
        detail["region"] = meta["region"]
        detail["territory_name"] = meta["territory_name"]
        detail["source_rows"]["data/territories.csv"] = _display_row(meta)
        for col in ("rep_capacity", "historical_revenue_usd", "opportunity_index"):
            if abs(meta[col] - row[col]) > FLOAT_TOLERANCE:
                notes.append(f"territories.csv {col} ({meta[col]:g}) differs from quota_plan.csv ({row[col]:g}).")
    else:
        notes.append("No matching row in territories.csv (or file unavailable).")

    detail["roster_summary"] = _roster_summary(tid, row, data_dir, thresholds, notes)
    detail["pipeline_sample"] = _pipeline_summary(tid, data_dir, notes)
    detail["calculation_steps"] = _calculation_steps(e)
    detail["data_notes"] = notes
    detail["threshold_label"] = THRESHOLD_LABEL
    detail["interpretation"] = (
        "Review required: " + " ".join(e["reasons"]) if e["flags"]
        else "No illustrative review rule was triggered for this territory."
    )
    detail["caveat"] = ("Historical revenue per rep is not a quota capacity model. A flag means "
                        "'review this input', not that the quota is wrong.")
    # Backwards compatibility with the starter kit: territory detail is always presented for review.
    detail["review_required"] = True
    detail["rule_triggered"] = bool(e["flags"])
    return detail


def _display_row(row: dict[str, Any]) -> dict[str, Any]:
    return {k: (int(v) if isinstance(v, float) and v.is_integer() else v) for k, v in row.items()}


def _roster_summary(tid, row, data_dir, thresholds: Thresholds, notes: list[str]) -> dict[str, Any] | None:
    """Aggregate-only roster view. Individual reps are never listed or ranked."""
    try:
        reps = [r for r in load_csv("sales_reps.csv", data_dir) if r["territory_id"] == tid]
    except DataValidationError as exc:
        notes.append(f"Roster unavailable: {exc}")
        return None
    active = [r for r in reps if r["status"].lower() == "active"]
    roster_rev = sum(r["historical_revenue_usd"] for r in reps)
    hist = row["historical_revenue_usd"]
    diff = roster_rev - hist
    diff_pct = diff / hist * 100 if hist > 0 else None
    if len(active) != row["rep_capacity"]:
        notes.append(f"Active reps on roster ({len(active)}) differ from planned capacity ({row['rep_capacity']:g}).")
    reconciles = diff_pct is not None and abs(diff_pct) < thresholds.reconciliation_pct - FLOAT_TOLERANCE
    if diff_pct is not None and not reconciles:
        notes.append(f"Roster historical revenue ({money(roster_rev)}) differs from territory historical revenue "
                     f"({money(hist)}) by {pct(diff_pct)}; confirm which source is authoritative.")
    return {
        "reps_on_roster": len(reps),
        "active_reps": len(active),
        "planned_capacity": row["rep_capacity"],
        "roster_historical_revenue_usd": round(roster_rev, 2),
        "territory_historical_revenue_usd": round(hist, 2),
        "difference_usd": round(diff, 2),
        "difference_pct": round(diff_pct, 2) if diff_pct is not None else None,
        "privacy_note": "Aggregate only. Individual reps are not listed or ranked.",
    }


def _pipeline_summary(tid, data_dir, notes: list[str]) -> dict[str, Any] | None:
    try:
        accts = [a for a in load_csv("accounts.csv", data_dir) if a["territory_id"] == tid]
    except DataValidationError as exc:
        notes.append(f"Pipeline sample unavailable: {exc}")
        return None
    by_stage: dict[str, dict[str, float]] = defaultdict(lambda: {"count": 0, "amount_usd": 0.0})
    for a in accts:
        by_stage[a["pipeline_stage"]]["count"] += 1
        by_stage[a["pipeline_stage"]]["amount_usd"] += a["opportunity_amount_usd"]
    return {
        "account_count": len(accts),
        "total_opportunity_usd": round(sum(a["opportunity_amount_usd"] for a in accts), 2),
        "by_stage": {k: {"count": int(v["count"]), "amount_usd": round(v["amount_usd"], 2)} for k, v in sorted(by_stage.items())},
        "caveat": "Synthetic pipeline sample only; not a complete pipeline, so coverage ratios are not meaningful.",
    }


def _calculation_steps(e: dict[str, Any]) -> list[str]:
    steps = [
        f"Variance = proposed quota {money(e['proposed_quota_usd'])} − target {money(e['company_target_usd'])} = {signed_money(e['variance_usd'])}",
        f"Variance % = variance ÷ target = {pct(e['variance_pct'])}",
    ]
    if e["quota_per_rep_usd"] is not None:
        steps += [
            f"Quota per rep = {money(e['proposed_quota_usd'])} ÷ {e['rep_capacity']:g} reps = {money(e['quota_per_rep_usd'])}",
            f"Historical revenue per rep = {money(e['historical_revenue_usd'])} ÷ {e['rep_capacity']:g} reps = {money(e['historical_revenue_per_rep_usd'])}",
        ]
        if e["quota_to_hist_per_rep_ratio"] is not None:
            steps.append(f"Ratio = quota per rep ÷ historical revenue per rep = {e['quota_to_hist_per_rep_ratio']:.3f}x")
    else:
        steps.append("Per-rep metrics not calculated: rep capacity is not positive.")
    steps.append(f"Opportunity index = {e['opportunity_index']:g} (from source data; not calculated)")
    return steps


def check_data_consistency(data_dir: Path | str | None = None,
                           thresholds: Thresholds = DEFAULT_THRESHOLDS) -> dict[str, Any]:
    """Cross-file reconciliation checks. Informational: these are data-quality notes, not exceptions."""
    plan = load_quota_plan(data_dir)
    findings: list[dict[str, Any]] = []
    files_checked = ["data/quota_plan.csv"]
    try:
        meta = {r["territory_id"]: r for r in load_csv("territories.csv", data_dir)}
        files_checked.append("data/territories.csv")
        for r in plan:
            m = meta.get(r["territory_id"])
            if m is None:
                findings.append({"territory_id": r["territory_id"], "check": "territory_metadata", "detail": "Missing from territories.csv"})
                continue
            for col in ("rep_capacity", "historical_revenue_usd", "opportunity_index"):
                if abs(m[col] - r[col]) > FLOAT_TOLERANCE:
                    findings.append({"territory_id": r["territory_id"], "check": f"{col}_mismatch",
                                     "detail": f"territories.csv {m[col]:g} vs quota_plan.csv {r[col]:g}"})
    except DataValidationError as exc:
        findings.append({"territory_id": "ALL", "check": "territories_file", "detail": str(exc)})
    try:
        reps = load_csv("sales_reps.csv", data_dir)
        files_checked.append("data/sales_reps.csv")
        count: dict[str, int] = defaultdict(int)
        rev: dict[str, float] = defaultdict(float)
        for rep in reps:
            if rep["status"].lower() == "active":
                count[rep["territory_id"]] += 1
            rev[rep["territory_id"]] += rep["historical_revenue_usd"]
        for r in plan:
            tid = r["territory_id"]
            if count[tid] != r["rep_capacity"]:
                findings.append({"territory_id": tid, "check": "capacity_vs_active_roster",
                                 "detail": f"{count[tid]} active reps vs planned capacity {r['rep_capacity']:g}"})
            hist = r["historical_revenue_usd"]
            if hist > 0:
                d = (rev[tid] - hist) / hist * 100
                if _ge(abs(d), thresholds.reconciliation_pct):
                    findings.append({"territory_id": tid, "check": "roster_revenue_reconciliation",
                                     "detail": f"Roster revenue {money(rev[tid])} vs territory history {money(hist)} ({pct(d)})"})
    except DataValidationError as exc:
        findings.append({"territory_id": "ALL", "check": "roster_file", "detail": str(exc)})
    return {
        "tool": "check_data_consistency",
        "files_checked": files_checked,
        "finding_count": len(findings),
        "findings": findings,
        "reconciliation_threshold_pct": thresholds.reconciliation_pct,
        "note": "Informational data-quality checks; they do not change exception flags.",
    }


# Information the planning dataset does not contain. These are schema facts about the
# demo CSVs (checked against the actual headers at runtime), not assumptions about the business.
_GAP_CATALOG = [
    ("Rep tenure, ramp status, and planned hires/leavers", ["ramp_status", "tenure_months", "start_date"]),
    ("Prior-year quota and attainment by territory", ["prior_year_quota_usd", "attainment_pct"]),
    ("Market size / total addressable market by territory", ["tam_usd", "market_size_usd"]),
    ("Full pipeline with win rates and expected close dates", ["close_date", "win_rate"]),
    ("Seasonality and product-mix assumptions", ["seasonality_factor", "product_line"]),
    ("Business owner's rationale for each proposed quota", ["rationale", "owner_notes"]),
    ("Approved methodology for converting opportunity index into quota", ["methodology"]),
]


def list_data_gaps(territory_id: str | None = None, data_dir: Path | str | None = None,
                   thresholds: Thresholds = DEFAULT_THRESHOLDS) -> dict[str, Any]:
    """What is missing before a quota change could be recommended. Read-only."""
    headers: set[str] = set()
    for name in REQUIRED_COLUMNS:
        try:
            path = _resolve_dir(data_dir) / name
            with open(path, newline="", encoding="utf-8-sig") as f:
                headers.update(h.strip() for h in (next(csv.reader(f), []) or []))
        except OSError:
            continue
    missing = [label for label, cols in _GAP_CATALOG if not any(c in headers for c in cols)]
    consistency = check_data_consistency(data_dir, thresholds)
    findings = consistency["findings"]
    scope = None
    if territory_id:
        try:
            scope = normalize_territory_id(territory_id)
        except ValueError as exc:
            return {"tool": "list_data_gaps", "error": f"Invalid territory_id: {exc}"}
        plan_ids = {r["territory_id"] for r in load_quota_plan(data_dir)}
        if scope not in plan_ids:
            return {"tool": "list_data_gaps", "error": f"Unknown territory_id: {scope}", "valid_ids": sorted(plan_ids)}
        findings = [f for f in findings if f["territory_id"] in (scope, "ALL")]
    return {
        "tool": "list_data_gaps",
        "territory_id": scope,
        "not_in_dataset": missing,
        "data_quality_findings": findings,
        "available_columns": sorted(headers),
        "conclusion": ("The dataset supports identifying inputs to review, but not recommending a specific "
                       "quota change. A planner needs the items listed as missing plus business-owner context."),
    }


def preview_quota_scenario(territory_id: str, alternative_quota_usd: float,
                           data_dir: Path | str | None = None,
                           thresholds: Thresholds = DEFAULT_THRESHOLDS) -> dict[str, Any]:
    """Hypothetical what-if. Calculates in memory only; NOTHING is written or applied."""
    try:
        tid = normalize_territory_id(territory_id)
    except ValueError as exc:
        return {"tool": "preview_quota_scenario", "error": f"Invalid territory_id: {exc}"}
    if isinstance(alternative_quota_usd, bool) or not isinstance(alternative_quota_usd, (int, float)):
        return {"tool": "preview_quota_scenario", "error": "alternative_quota_usd must be a number"}
    if not (0 < alternative_quota_usd < 1e12) or math.isnan(alternative_quota_usd):
        return {"tool": "preview_quota_scenario", "error": "alternative_quota_usd must be greater than 0 and less than 1e12"}
    rows = load_quota_plan(data_dir)
    row = next((r for r in rows if r["territory_id"] == tid), None)
    if row is None:
        return {"tool": "preview_quota_scenario", "error": f"Unknown territory_id: {tid}",
                "valid_ids": [r["territory_id"] for r in rows]}
    before = evaluate_territory(row, thresholds)
    after = evaluate_territory({**row, "proposed_quota_usd": float(alternative_quota_usd)}, thresholds)
    plan = summarize_plan(data_dir)
    new_total = plan["proposed_quota_total_usd"] - row["proposed_quota_usd"] + float(alternative_quota_usd)
    new_gap = new_total - plan["company_target_usd"]
    return {
        "tool": "preview_quota_scenario",
        "hypothetical": True,
        "applied": False,
        "territory_id": tid,
        "current_proposed_quota_usd": round(row["proposed_quota_usd"], 2),
        "alternative_quota_usd": round(float(alternative_quota_usd), 2),
        "territory_change_usd": round(float(alternative_quota_usd) - row["proposed_quota_usd"], 2),
        "current_flags": before["flags"],
        "flags_under_alternative": after["flags"],
        "alternative_variance_pct": after["variance_pct"],
        "plan_total_current_usd": plan["proposed_quota_total_usd"],
        "plan_total_under_alternative_usd": round(new_total, 2),
        "plan_gap_current_usd": plan["quota_minus_target_usd"],
        "plan_gap_under_alternative_usd": round(new_gap, 2),
        "note": "Hypothetical calculation only. The quota plan has not been changed.",
    }


def source_fingerprint(data_dir: Path | str | None = None) -> dict[str, str]:
    """SHA-256 of each project CSV; used to prove draft/review actions never change source data."""
    d = _resolve_dir(data_dir)
    out = {}
    for name in sorted(REQUIRED_COLUMNS):
        p = d / name
        out[f"data/{name}"] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "missing"
    return out


def validate_all_data(data_dir: Path | str | None = None) -> dict[str, Any]:
    """Validate every project CSV; returns a status per file instead of raising."""
    status = {}
    for name in REQUIRED_COLUMNS:
        try:
            rows = load_quota_plan(data_dir) if name == "quota_plan.csv" else load_csv(name, data_dir)
            status[f"data/{name}"] = {"ok": True, "rows": len(rows)}
        except DataValidationError as exc:
            status[f"data/{name}"] = {"ok": False, "error": str(exc)}
    return {"tool": "validate_all_data", "all_ok": all(v["ok"] for v in status.values()), "files": status}


def run_all(data_dir: Path | str | None = None) -> dict[str, Any]:
    return {"plan_summary": summarize_plan(data_dir), "planning_exceptions": find_planning_exceptions(data_dir)}


if __name__ == "__main__":
    print(json.dumps(run_all(), indent=2))
