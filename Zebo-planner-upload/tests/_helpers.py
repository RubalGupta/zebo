"""Shared test helpers: isolated copies of the data and a temporary demo-state directory."""
import csv
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
DATA = ROOT / "data"

QUOTA_HEADER = ["territory_id", "company_target_usd", "proposed_quota_usd", "rep_capacity",
                "historical_revenue_usd", "opportunity_index"]


class TempDirs:
    """Context manager: copy of data/ + empty state dir; NORBAE_STATE_DIR points at the temp state."""

    def __enter__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="norbae_test_"))
        self.data = self.tmp / "data"
        shutil.copytree(DATA, self.data)
        self.state = self.tmp / "state"
        self._old = os.environ.get("NORBAE_STATE_DIR")
        os.environ["NORBAE_STATE_DIR"] = str(self.state)
        return self

    def write_quota_plan(self, rows, header=QUOTA_HEADER):
        with open(self.data / "quota_plan.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)

    def __exit__(self, *exc):
        if self._old is None:
            os.environ.pop("NORBAE_STATE_DIR", None)
        else:
            os.environ["NORBAE_STATE_DIR"] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)
