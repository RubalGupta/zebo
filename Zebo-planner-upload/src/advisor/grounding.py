"""Check that every $ and % figure in an Advisor answer is traceable to tool output (rounding-aware)."""
from __future__ import annotations

import re
from typing import Any

_MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.(\d+))?)\s?([KMB])?", re.I)
_PCT = re.compile(r"([+-]?\d+(?:\.(\d+))?)%")
_UNIT = {"K": 1e3, "M": 1e6, "B": 1e9}


def _numbers(obj: Any, out: list[float]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        if obj == obj:  # not NaN
            out.append(abs(float(obj)))
    elif isinstance(obj, dict):
        for v in obj.values():
            _numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _numbers(v, out)
    elif isinstance(obj, str):
        for m in _MONEY.finditer(obj):
            out.append(float(m.group(1).replace(",", "")) * _UNIT.get((m.group(3) or "").upper(), 1))
        for m in _PCT.finditer(obj):
            out.append(abs(float(m.group(1))))
        for m in re.finditer(r"\b\d+(?:\.\d+)?\b", obj):
            out.append(float(m.group(0)))


def check(answer: str, tool_results: list[Any], extra_numbers: list[float] | None = None) -> dict[str, Any]:
    known: list[float] = list(extra_numbers or [])
    _numbers(tool_results, known)
    untraceable, n = [], 0
    for m in _MONEY.finditer(answer):
        n += 1
        unit = _UNIT.get((m.group(3) or "").upper(), 1)
        decimals = len(m.group(2) or "")
        v = float(m.group(1).replace(",", "")) * unit
        tol = 0.5 * unit / (10 ** decimals) + 1e-6
        if not any(abs(v - k) <= tol for k in known):
            untraceable.append(m.group(0).strip())
    for m in _PCT.finditer(answer):
        n += 1
        decimals = len(m.group(2) or "")
        v = abs(float(m.group(1)))
        tol = 0.5 / (10 ** decimals) + 1e-6
        if not any(abs(v - k) <= tol for k in known):
            untraceable.append(m.group(0))
    return {"figures_checked": n, "untraceable": untraceable, "all_traceable": not untraceable}
