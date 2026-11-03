"""Demo personas and access scope. Every planning tool filters to the persona's scope (enforced in code)."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from planning.data import PlanningData

LEVELS = ["bu", "division", "area", "territory", "account"]
CHILD = {"bu": "division", "division": "area", "area": "territory", "territory": "account"}
ID_COL = {"bu": "bu_id", "division": "division_id", "area": "area_id", "territory": "territory_id", "account": "account_id"}


@dataclass(frozen=True)
class Persona:
    id: str
    label: str
    level: str          # bu | division | area
    scope_id: str
    seller_id: str

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "level": self.level, "scope_id": self.scope_id}


def personas(data: PlanningData) -> list[Persona]:
    h = data.hierarchy
    s = data.sellers.set_index("seller_id")["seller_name"]
    out = []
    bu = h[h["level"] == "bu"].iloc[0]
    out.append(Persona(f"P-{bu['id']}", f"{s.get(bu['leader_id'], 'VP')} · VP Sales — {bu['name']} (all)", "bu",
                       bu["id"], bu["leader_id"]))
    for _, r in h[h["level"] == "division"].iterrows():
        out.append(Persona(f"P-{r['id']}", f"{s.get(r['leader_id'], '')} · Division Leader — {r['name']}", "division",
                           r["id"], r["leader_id"]))
    for _, r in h[h["level"] == "area"].iterrows():
        div = h.loc[h["id"] == r["parent_id"], "name"].iloc[0]
        out.append(Persona(f"P-{r['id']}", f"{s.get(r['leader_id'], '')} · Area Manager — {r['name']} ({div})", "area",
                           r["id"], r["leader_id"]))
    return out


def persona_by_id(data: PlanningData, pid: str) -> Persona:
    for p in personas(data):
        if p.id == pid:
            return p
    raise KeyError(f"Unknown persona: {pid}")


def in_scope(persona: Persona, row: pd.Series | dict) -> bool:
    if persona.level == "bu":
        return True
    return str(row.get(ID_COL[persona.level], "")) == persona.scope_id


def filter_scope(persona: Persona, df: pd.DataFrame) -> pd.DataFrame:
    if persona.level == "bu":
        return df
    return df[df[ID_COL[persona.level]] == persona.scope_id]
