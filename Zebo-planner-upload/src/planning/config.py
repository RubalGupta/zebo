"""Illustrative planning rules for the FY27 demo. NOT industry standards — business owners must validate them."""
from __future__ import annotations

from dataclasses import asdict, dataclass

LABEL = "Illustrative demo rules — not industry standards; validate with business owners."

MOTION_ORDER = ["Deep", "Expansion", "Greenfield"]
COVERAGE_LEVEL = {"Deep": 3, "Expansion": 2, "Greenfield": 1}   # higher = more seller attention per account


@dataclass(frozen=True)
class PlanningRules:
    # accounts a single AE can cover well, by territory motion (min, max)
    capacity_deep: tuple[int, int] = (2, 5)
    capacity_expansion: tuple[int, int] = (3, 8)
    capacity_greenfield: tuple[int, int] = (4, 12)
    late_stage_probability: float = 0.6           # Negotiation/Commit and above
    late_stage_risk_usd: float = 250_000          # late-stage pipeline closing soon that a move could disrupt
    large_pipeline_usd: float = 1_000_000         # "big opportunity" note
    near_term_periods: tuple[str, ...] = ("FY26-Dec", "FY27-Q1")
    declining_yoy_pct: float = -15.0
    growing_yoy_pct: float = 15.0
    tailwind_outlook_pct: float = 7.0
    headwind_outlook_pct: float = -3.0
    recommend_score: int = 2                      # score >= this -> Recommended
    reject_score: int = -2                        # score <= this -> Not recommended

    def capacity(self, motion: str) -> tuple[int, int]:
        return {"Deep": self.capacity_deep, "Expansion": self.capacity_expansion,
                "Greenfield": self.capacity_greenfield}.get(motion, (1, 999))

    def to_dict(self) -> dict:
        return asdict(self)


DEFAULT_RULES = PlanningRules()
