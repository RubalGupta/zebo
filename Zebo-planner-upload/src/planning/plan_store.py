"""Draft FY27 plan: audited account moves stored under local_state/ (never in data/).

The source assignment (data/fy27/accounts.csv) is the FY26 book. The draft plan is a list of moves applied on
top of it. Moves can be reverted; every change records who, when, why and the Advisor's verdict at the time.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from planning.data import PlanningData

_LOCK = threading.RLock()
PLAN_FILE = "fy27_plan.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _state_dir() -> Path:
    root = Path(__file__).resolve().parents[2]
    return Path(os.environ.get("NORBAE_STATE_DIR", root / "local_state"))


class PlanStore:
    def __init__(self, base: Path | str | None = None):
        self.base = Path(base) if base else _state_dir()

    @property
    def path(self) -> Path:
        return self.base / PLAN_FILE

    def load(self) -> dict[str, Any]:
        with _LOCK:
            if self.path.is_file():
                try:
                    return json.loads(self.path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    pass
            return {"plan": "FY27", "status": "DRAFT", "created_at": now_iso(), "moves": []}

    def _save(self, plan: dict[str, Any]) -> None:
        self.base.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.base, prefix=".plan_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2)
        os.replace(tmp, self.path)

    def moves(self, active_only: bool = True) -> list[dict[str, Any]]:
        mv = self.load()["moves"]
        return [m for m in mv if m["status"] == "active"] if active_only else mv

    def add_move(self, *, account_id: str, from_territory_id: str, to_territory_id: str, from_owner_id: str,
                 to_owner_id: str, reason: str, verdict: str, score: int, by: str) -> dict[str, Any]:
        reason = (reason or "").strip()[:500]
        if not reason:
            raise ValueError("A reason is required for every plan change")
        if from_territory_id == to_territory_id:
            raise ValueError("The account is already in that territory")
        mv = {"move_id": "MV-" + uuid.uuid4().hex[:8].upper(), "account_id": account_id,
              "from_territory_id": from_territory_id, "to_territory_id": to_territory_id,
              "from_owner_id": from_owner_id, "to_owner_id": to_owner_id, "reason": reason,
              "advisor_verdict": verdict, "advisor_score": score, "by": by[:120], "at": now_iso(),
              "status": "active"}
        with _LOCK:
            plan = self.load()
            if plan.get("status") != "DRAFT":
                raise ValueError("The plan is locked; changes need a new draft")
            plan["moves"].append(mv)
            self._save(plan)
        return mv

    def revert(self, move_id: str, by: str) -> dict[str, Any]:
        with _LOCK:
            plan = self.load()
            pos = next((i for i, x in enumerate(plan["moves"]) if x["move_id"] == move_id), None)
            if pos is None:
                raise KeyError(f"Unknown move: {move_id}")
            m = plan["moves"][pos]
            if m["status"] != "active":
                raise ValueError("Move already reverted")
            later = [x for x in plan["moves"][pos + 1:] if x["status"] == "active" and x["account_id"] == m["account_id"]]
            if later:
                raise ValueError("Revert the account's later move first")
            m["status"], m["reverted_by"], m["reverted_at"] = "reverted", by[:120], now_iso()
            self._save(plan)
            return m

    def reset(self) -> None:
        with _LOCK:
            if self.path.exists():
                self.path.unlink()


def effective_accounts(data: PlanningData, store: PlanStore | None) -> pd.DataFrame:
    """Accounts with the draft plan's moves applied (territory, owner, area, division), plus moved flags."""
    acc = data.accounts.copy()
    acc["fy26_territory_id"] = acc["territory_id"]
    acc["moved"] = False
    if store is None:
        return acc
    terr = data.territories.set_index("territory_id")
    owners = data.sellers.set_index("seller_id")["seller_name"]
    idx = acc.set_index("account_id").index
    for m in store.moves():
        if m["account_id"] not in idx or m["to_territory_id"] not in terr.index:
            continue
        t = terr.loc[m["to_territory_id"]]
        sel = acc["account_id"] == m["account_id"]
        acc.loc[sel, ["territory_id", "owner_id", "area_id", "division_id"]] = [
            m["to_territory_id"], t["owner_id"], t["area_id"], t["division_id"]]
        acc.loc[sel, "owner_name"] = owners.get(t["owner_id"], "")
    acc["moved"] = acc["territory_id"] != acc["fy26_territory_id"]
    return acc
