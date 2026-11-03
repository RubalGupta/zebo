"""Load and validate the FY27 demo dataset; derive account-level metrics."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = ROOT / "data" / "fy27"

REQUIRED = {
    "hierarchy.csv": ["level", "id", "name", "parent_id", "leader_id"],
    "territories.csv": ["territory_id", "territory_name", "area_id", "division_id", "bu_id", "motion", "owner_id"],
    "sellers.csv": ["seller_id", "seller_name", "role", "division_id", "area_id", "territory_id", "manager_id",
                    "motion_focus", "tenure_months", "status"],
    "accounts.csv": ["account_id", "account_name", "industry", "employee_band", "motion", "territory_id", "owner_id",
                     "area_id", "division_id", "bu_id"],
    "revenue_monthly.csv": ["account_id", "fiscal_year", "month", "revenue_usd"],
    "pipeline.csv": ["opportunity_id", "account_id", "stage", "probability", "amount_usd", "close_period"],
    "account_signals.csv": ["account_id", "signal", "impact", "intent_score", "as_of", "source"],
    "industry_outlook.csv": ["industry", "fy27_outlook_pct", "note"],
}


class PlanningDataError(ValueError):
    pass


@dataclass
class PlanningData:
    hierarchy: pd.DataFrame
    territories: pd.DataFrame
    sellers: pd.DataFrame
    accounts: pd.DataFrame        # with derived metrics
    revenue: pd.DataFrame
    pipeline: pd.DataFrame
    signals: pd.DataFrame
    outlook: pd.DataFrame
    ytd_months: int

    def name_of(self, entity_id: str) -> str:
        h = self.hierarchy[self.hierarchy["id"] == entity_id]
        if len(h):
            return str(h.iloc[0]["name"])
        t = self.territories[self.territories["territory_id"] == entity_id]
        if len(t):
            return str(t.iloc[0]["territory_name"])
        a = self.accounts[self.accounts["account_id"] == entity_id]
        if len(a):
            return str(a.iloc[0]["account_name"])
        s = self.sellers[self.sellers["seller_id"] == entity_id]
        if len(s):
            return str(s.iloc[0]["seller_name"])
        return entity_id


def data_dir() -> Path:
    return Path(os.environ.get("NORBAE_FY27_DATA_DIR", DEFAULT_DIR))


def _read(d: Path, name: str) -> pd.DataFrame:
    p = d / name
    if not p.is_file():
        raise PlanningDataError(f"Required data file 'data/fy27/{name}' was not found")
    try:
        df = pd.read_csv(p, dtype={"parent_id": str, "territory_id": str, "manager_id": str, "area_id": str,
                                   "division_id": str, "leader_id": str}, keep_default_na=False)
    except Exception as exc:  # pandas raises several types
        raise PlanningDataError(f"'data/fy27/{name}' could not be parsed: {exc}") from None
    missing = [c for c in REQUIRED[name] if c not in df.columns]
    if missing:
        raise PlanningDataError(f"'data/fy27/{name}' is missing column(s): {', '.join(missing)}")
    if name not in ("account_signals.csv",) and df.empty:
        raise PlanningDataError(f"'data/fy27/{name}' contains no rows")
    return df


def load(d: Path | str | None = None) -> PlanningData:
    d = Path(d) if d else data_dir()
    t = {name: _read(d, name) for name in REQUIRED}
    rev = t["revenue_monthly.csv"]
    for col in ("revenue_usd", "month"):
        rev[col] = pd.to_numeric(rev[col], errors="coerce")
    if rev["revenue_usd"].isna().any() or rev["month"].isna().any():
        raise PlanningDataError("'data/fy27/revenue_monthly.csv' has non-numeric revenue_usd or month values")
    pipe = t["pipeline.csv"]
    for col in ("probability", "amount_usd"):
        pipe[col] = pd.to_numeric(pipe[col], errors="coerce")
    if pipe[["probability", "amount_usd"]].isna().any().any():
        raise PlanningDataError("'data/fy27/pipeline.csv' has non-numeric probability or amount_usd values")
    acc = t["accounts.csv"].copy()
    if acc["account_id"].duplicated().any():
        raise PlanningDataError("'data/fy27/accounts.csv' has duplicate account_id values")
    unknown_t = set(acc["territory_id"]) - set(t["territories.csv"]["territory_id"])
    if unknown_t:
        raise PlanningDataError(f"accounts reference unknown territories: {', '.join(sorted(unknown_t)[:5])}")

    ytd = int(rev.loc[rev["fiscal_year"] == "FY26", "month"].max())
    fy25 = rev[rev["fiscal_year"] == "FY25"]
    fy26 = rev[rev["fiscal_year"] == "FY26"]
    m = pd.DataFrame({"account_id": acc["account_id"]}).set_index("account_id")
    m["fy25_total"] = fy25.groupby("account_id")["revenue_usd"].sum()
    m["fy25_ytd"] = fy25[fy25["month"] <= ytd].groupby("account_id")["revenue_usd"].sum()
    m["fy26_ytd"] = fy26[fy26["month"] <= ytd].groupby("account_id")["revenue_usd"].sum()
    m = m.fillna(0.0)
    m["yoy_pct"] = (m["fy26_ytd"] / m["fy25_ytd"] - 1).where(m["fy25_ytd"] > 0) * 100
    pipe = pipe.copy()
    pipe["weighted_usd"] = pipe["amount_usd"] * pipe["probability"]
    g = pipe.groupby("account_id")
    m["open_pipeline"] = g["amount_usd"].sum()
    m["weighted_pipeline"] = g["weighted_usd"].sum()
    m["largest_opp"] = g["amount_usd"].max()
    m[["open_pipeline", "weighted_pipeline", "largest_opp"]] = m[["open_pipeline", "weighted_pipeline", "largest_opp"]].fillna(0.0)
    acc = acc.merge(m.reset_index(), on="account_id", how="left")
    sig = t["account_signals.csv"]
    if len(sig):
        first = sig.drop_duplicates("account_id")[["account_id", "signal", "impact", "intent_score"]]
        acc = acc.merge(first, on="account_id", how="left")
    else:
        acc["signal"], acc["impact"], acc["intent_score"] = "", "", None
    acc[["signal", "impact"]] = acc[["signal", "impact"]].fillna("")
    out = t["industry_outlook.csv"]
    out["fy27_outlook_pct"] = pd.to_numeric(out["fy27_outlook_pct"], errors="coerce")
    acc = acc.merge(out[["industry", "fy27_outlook_pct"]], on="industry", how="left")
    sellers = t["sellers.csv"]
    acc = acc.merge(sellers[["seller_id", "seller_name"]].rename(columns={"seller_id": "owner_id", "seller_name": "owner_name"}),
                    on="owner_id", how="left")
    return PlanningData(t["hierarchy.csv"], t["territories.csv"], sellers, acc, rev, pipe, sig, out, ytd)
