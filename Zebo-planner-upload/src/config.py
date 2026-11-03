"""Central configuration for the Norbae AI Annual Sales Planning Advisor.

ALL THRESHOLDS HERE ARE ILLUSTRATIVE DEMO THRESHOLDS.
They are not industry standards. A business owner must validate them before
any real use. They are configurable at runtime (per request) and never change
the source CSV data.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
# Local demo state (draft requests, audit log, manual eval records). Gitignored.
DEFAULT_STATE_DIR = PROJECT_ROOT / "local_state"

THRESHOLD_LABEL = "Illustrative demo thresholds — not industry standards; require business-owner validation."

# Tolerance used for ">=" comparisons so that values exactly on a threshold
# (e.g. a variance of exactly 5%) are treated consistently despite floating point.
FLOAT_TOLERANCE = 1e-9

MAX_TOOL_CALLS_PER_QUESTION = 3


@dataclass(frozen=True)
class Thresholds:
    """Illustrative review thresholds. A flag means "review this input", not "this quota is wrong"."""

    variance_pct: float = 5.0            # flag when |proposed - target| / target >= 5%
    quota_to_hist_per_rep_ratio: float = 1.5  # flag when quota/rep ÷ historical revenue/rep >= 1.5
    opportunity_index_min: float = 0.5  # flag when opportunity index < 0.5
    opportunity_index_max: float = 1.5  # flag when opportunity index > 1.5
    reconciliation_pct: float = 1.0     # informational: rep roster revenue vs territory history

    def validate(self) -> "Thresholds":
        if not (0 < self.variance_pct <= 100):
            raise ValueError("variance_pct must be > 0 and <= 100")
        if not (0 < self.quota_to_hist_per_rep_ratio <= 100):
            raise ValueError("quota_to_hist_per_rep_ratio must be > 0 and <= 100")
        if not (0 <= self.opportunity_index_min < self.opportunity_index_max <= 100):
            raise ValueError("opportunity index range must satisfy 0 <= min < max <= 100")
        if not (0 <= self.reconciliation_pct <= 100):
            raise ValueError("reconciliation_pct must be between 0 and 100")
        return self

    def to_dict(self) -> dict[str, float]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, values: dict[str, Any] | None) -> "Thresholds":
        """Build thresholds from user-supplied values, ignoring unknown keys, validating types."""
        if not values:
            return cls()
        allowed = {f.name for f in fields(cls)}
        clean: dict[str, float] = {}
        for key, raw in values.items():
            if key not in allowed or raw in (None, ""):
                continue
            try:
                clean[key] = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"Threshold '{key}' must be numeric")
        return cls(**clean).validate()


DEFAULT_THRESHOLDS = Thresholds()


def data_dir() -> Path:
    """Data directory. Overridable with NORBAE_DATA_DIR (used by tests and demos)."""
    override = os.environ.get("NORBAE_DATA_DIR")
    return Path(override) if override else DEFAULT_DATA_DIR


def state_dir() -> Path:
    override = os.environ.get("NORBAE_STATE_DIR")
    return Path(override) if override else DEFAULT_STATE_DIR
