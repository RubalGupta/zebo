"""Generate the FY27 planning demo dataset (100% synthetic, deterministic).

    python tools/generate_fy27_data.py      # writes data/fy27/*.csv

Shape (as requested for the demo): 1 BU, 5 divisions, 6-8 areas per division,
~800 accounts, 350 sellers in various roles. Company, account and person names
are generated from syllables and are fictional. Market signals are fictional.

Deliberate planning issues are seeded so the tools have something to find:
- ~8% of accounts sit in a territory whose sales motion does not match theirs;
- some accounts are declining, some have large open pipeline;
- some territories are over/under-loaded.
"""
from __future__ import annotations

import csv
import random
from pathlib import Path

SEED = 2027
OUT = Path(__file__).resolve().parents[1] / "data" / "fy27"
R = random.Random(SEED)

BU = ("BU1", "Enterprise Solutions")
DIVISIONS = [
    ("NAW", "North America West", ["Bay Area", "Pacific Northwest", "Southern California", "Mountain", "Southwest", "Northern California", "Hawaii & Alaska"]),
    ("NAE", "North America East", ["New York Metro", "New England", "Mid-Atlantic", "Southeast", "Florida", "Carolinas", "Ohio Valley", "Great Lakes"]),
    ("CLA", "Central & LATAM", ["Texas", "Plains", "Midwest", "Mexico", "Brazil", "Andean"]),
    ("EMEA", "EMEA", ["UK & Ireland", "DACH", "France & Benelux", "Nordics", "Southern Europe", "Middle East & Africa", "Central & Eastern Europe"]),
    ("APAC", "APAC", ["ANZ", "Japan", "Greater China", "India", "Southeast Asia", "Korea"]),
]
MOTIONS = {"D": "Deep", "E": "Expansion", "G": "Greenfield"}
INDUSTRIES = [  # fictional FY27 outlook % (demo market signal)
    ("Healthcare", 9.0, "Tailwind: digital care investment (fictional signal)"),
    ("Financial Services", 5.0, "Steady: modernization budgets (fictional signal)"),
    ("Retail", -3.0, "Headwind: margin pressure (fictional signal)"),
    ("Manufacturing", 2.0, "Flat: selective automation spend (fictional signal)"),
    ("Public Sector", 4.0, "Moderate: multi-year programs (fictional signal)"),
    ("Media", -5.0, "Headwind: ad market softness (fictional signal)"),
    ("Energy", 7.0, "Tailwind: grid and transition projects (fictional signal)"),
    ("Technology", 11.0, "Strong tailwind: AI infrastructure spend (fictional signal)"),
    ("Logistics", 1.0, "Flat: freight normalization (fictional signal)"),
    ("Education", 3.0, "Moderate: platform consolidation (fictional signal)"),
]
SEASONALITY = [0.06, 0.07, 0.09, 0.07, 0.08, 0.10, 0.07, 0.07, 0.10, 0.08, 0.09, 0.12]
SYL_A = ["Al", "Bri", "Cor", "Dal", "El", "Fen", "Gal", "Hal", "Is", "Jor", "Kel", "Lum", "Mar", "Nor", "Or", "Pel",
         "Quin", "Ral", "Sel", "Tor", "Ul", "Ver", "Wyn", "Xan", "Yor", "Zel", "Ash", "Bel", "Cal", "Dor"]
SYL_B = ["vane", "mere", "tris", "dex", "nova", "ridge", "brook", "stone", "wick", "field", "gate", "lyn", "ora", "quist",
         "ton", "vale", "wren", "mont", "core", "sphere"]
SUFFIX = {"Healthcare": ["Health", "Care", "Medical"], "Financial Services": ["Financial", "Capital", "Bancorp"],
          "Retail": ["Retail", "Stores", "Brands"], "Manufacturing": ["Industries", "Manufacturing", "Works"],
          "Public Sector": ["Agency", "Authority", "Services"], "Media": ["Media", "Studios", "Networks"],
          "Energy": ["Energy", "Power", "Utilities"], "Technology": ["Systems", "Labs", "Software"],
          "Logistics": ["Logistics", "Freight", "Transport"], "Education": ["Learning", "University", "Academy"]}
FIRST = ["Avery", "Blake", "Casey", "Dana", "Ellis", "Finley", "Gray", "Harper", "Indy", "Jordan", "Kai", "Logan",
         "Morgan", "Noel", "Oakley", "Parker", "Quinn", "Reese", "Sage", "Taylor", "Uma", "Val", "Wren", "Yael",
         "Zion", "Arden", "Brook", "Cam", "Drew", "Emery", "Frankie", "Hollis", "Jules", "Kendall", "Lane", "Marlo",
         "Nico", "Remy", "Shay", "Tatum"]
LAST = ["Abernet", "Brightwell", "Castellan", "Dunmore", "Everly", "Fairhurst", "Galloway", "Hartigan", "Ingram",
        "Jessop", "Kerrigan", "Lindqvist", "Marlowe", "Northcott", "Okafor", "Pemberton", "Quayle", "Ravensworth",
        "Sallow", "Thorne", "Underhill", "Varga", "Whitlock", "Yardley", "Zamora", "Ashdown", "Bellamy", "Crane",
        "Delacroix", "Esterly", "Fenwick", "Grisham", "Holloway", "Iverson", "Jarrow", "Kingsley", "Lockridge",
        "Montague", "Nakamura", "Osei"]


def fname(used: set[str]) -> str:
    while True:
        n = f"{R.choice(FIRST)} {R.choice(LAST)}"
        if n not in used:
            used.add(n)
            return n


def write(name: str, rows: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / name, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    people_used: set[str] = set()
    hierarchy, territories, sellers = [], [], []
    sid = iter(range(1, 10_000))

    def seller(role, div, area, terr, manager, motion=""):
        s = {"seller_id": f"S{next(sid):04d}", "seller_name": fname(people_used), "role": role,
             "division_id": div, "area_id": area, "territory_id": terr, "manager_id": manager,
             "motion_focus": motion, "tenure_months": R.choice([3, 6, 9, 14, 20, 28, 36, 48, 60, 84]),
             "status": "Active", "data_origin": "Synthetic"}
        sellers.append(s)
        return s

    vp = seller("VP Sales", "", "", "", "")
    for div_id, div_name, areas in DIVISIONS:
        rvp = seller("Division Leader (RVP)", div_id, "", "", vp["seller_id"])
        hierarchy.append({"level": "division", "id": div_id, "name": div_name, "parent_id": BU[0], "leader_id": rvp["seller_id"]})
        for ai, area_name in enumerate(areas, 1):
            area_id = f"{div_id}-A{ai:02d}"
            mgr = seller("Area Sales Manager", div_id, area_id, "", rvp["seller_id"])
            hierarchy.append({"level": "area", "id": area_id, "name": area_name, "parent_id": div_id, "leader_id": mgr["seller_id"]})
            plan = ["D", "E", "E", "G"] + (["G"] if R.random() < 0.55 else [])
            counters = {"D": 0, "E": 0, "G": 0}
            for m in plan:
                counters[m] += 1
                tid = f"{area_id}-{m}{counters[m]}"
                ae = seller("Account Executive", div_id, area_id, tid, mgr["seller_id"], MOTIONS[m])
                territories.append({"territory_id": tid, "territory_name": f"{area_name} {MOTIONS[m]} {counters[m]}",
                                    "area_id": area_id, "division_id": div_id, "bu_id": BU[0], "motion": MOTIONS[m],
                                    "owner_id": ae["seller_id"]})
    hierarchy.insert(0, {"level": "bu", "id": BU[0], "name": BU[1], "parent_id": "", "leader_id": vp["seller_id"]})

    # overlay roles to reach exactly 350 sellers
    area_rows = [h for h in hierarchy if h["level"] == "area"]
    overlay_roles = [("Solutions Engineer", 0.30), ("Customer Success Manager", 0.38), ("Sales Development Rep", 0.32)]
    remaining = 350 - len(sellers)
    for k in range(remaining):
        r = R.random()
        role = "Solutions Engineer" if r < 0.30 else "Customer Success Manager" if r < 0.68 else "Sales Development Rep"
        a = area_rows[k % len(area_rows)]
        seller(role, a["parent_id"], a["id"], "", a["leader_id"])

    # accounts
    terr_by_area_motion: dict[tuple[str, str], list[dict]] = {}
    for t in territories:
        terr_by_area_motion.setdefault((t["area_id"], t["motion"]), []).append(t)
    targets = {"Deep": 110, "Expansion": 330, "Greenfield": 360}
    used_names: set[str] = set()
    accounts, revenue, pipeline, signals = [], [], [], []
    industries = {i[0]: i for i in INDUSTRIES}
    opp_id = iter(range(1, 100_000))
    aid = 0
    for motion, n in targets.items():
        for _ in range(n):
            aid += 1
            ind = R.choice(INDUSTRIES)[0]
            while True:
                nm = f"{R.choice(SYL_A)}{R.choice(SYL_B)} {R.choice(SUFFIX[ind])}"
                if nm not in used_names:
                    used_names.add(nm)
                    break
            area = R.choice(area_rows)
            home = terr_by_area_motion.get((area["id"], motion)) or terr_by_area_motion[(area["id"], "Expansion")]
            terr = R.choice(home)
            misfit = R.random() < 0.08
            if misfit:  # deliberate impurity: place in a territory of another motion within the same area
                others = [t for t in territories if t["area_id"] == area["id"] and t["motion"] != motion]
                terr = R.choice(others)
            if motion == "Deep":
                base = R.lognormvariate(15.1, 0.45)          # ~ $3.6M median
                employees = R.choice(["10,000+", "5,000-10,000"])
            elif motion == "Expansion":
                base = R.lognormvariate(13.0, 0.6)           # ~ $440k median
                employees = R.choice(["1,000-5,000", "5,000-10,000", "500-1,000"])
            else:
                base = 0.0 if R.random() < 0.6 else R.uniform(8_000, 90_000)
                employees = R.choice(["500-1,000", "1,000-5,000", "100-500"])
            outlook = industries[ind][1] / 100
            growth = outlook + R.gauss(0.04, 0.12)
            if R.random() < 0.13:
                growth = R.uniform(-0.35, -0.12)              # declining accounts
            if motion == "Greenfield" and base == 0 and R.random() < 0.25:
                base_fy26 = R.uniform(5_000, 60_000)          # newly landed this year
            else:
                base_fy26 = base * (1 + growth)
            acc = {"account_id": f"A{aid:04d}", "account_name": nm, "industry": ind, "employee_band": employees,
                   "motion": motion, "territory_id": terr["territory_id"], "owner_id": terr["owner_id"],
                   "area_id": terr["area_id"], "division_id": terr["division_id"], "bu_id": BU[0],
                   "data_origin": "Synthetic"}
            accounts.append(acc)
            for fy, total, months in (("FY25", base, 12), ("FY26", base_fy26, 11)):
                for m in range(months):
                    amt = total * SEASONALITY[m] * R.uniform(0.9, 1.1) if total else 0.0
                    revenue.append({"account_id": acc["account_id"], "fiscal_year": fy, "month": m + 1,
                                    "revenue_usd": round(amt, 2)})
            # pipeline
            n_opps = {"Deep": R.randint(2, 6), "Expansion": R.randint(0, 3), "Greenfield": R.choice([0, 0, 1, 1, 2])}[motion]
            for _o in range(n_opps):
                scale = max(base_fy26, 60_000)
                amt = scale * R.uniform(0.05, 0.35)
                if motion != "Greenfield" and R.random() < 0.05:
                    amt = R.uniform(1_500_000, 6_000_000)       # large opportunity
                stage, prob = R.choice([("Prospecting", 0.1), ("Qualified", 0.2), ("Proposal", 0.4),
                                        ("Negotiation", 0.6), ("Commit", 0.8)])
                close = R.choice(["FY26-Dec", "FY27-Q1", "FY27-Q1", "FY27-Q2", "FY27-Q3", "FY27-Q4"])
                pipeline.append({"opportunity_id": f"O{next(opp_id):05d}", "account_id": acc["account_id"],
                                 "stage": stage, "probability": prob, "amount_usd": round(amt, 2),
                                 "close_period": close, "data_origin": "Synthetic"})
            # account-level market signals (fictional)
            if R.random() < 0.35:
                kind, impact = R.choice([("Expansion announcement", "positive"), ("Hiring surge", "positive"),
                                         ("New funding round", "positive"), ("Leadership change", "neutral"),
                                         ("Cost-cutting program", "negative"), ("Merger announced", "neutral"),
                                         ("Layoffs reported", "negative")])
                signals.append({"account_id": acc["account_id"], "signal": kind, "impact": impact,
                                "intent_score": R.randint(20, 95), "as_of": "2026-11",
                                "source": "Synthetic market signal (fictional)"})

    write("hierarchy.csv", hierarchy)
    write("territories.csv", territories)
    write("sellers.csv", sellers)
    write("accounts.csv", accounts)
    write("revenue_monthly.csv", revenue)
    write("pipeline.csv", pipeline)
    write("account_signals.csv", signals)
    write("industry_outlook.csv", [{"industry": i, "fy27_outlook_pct": o, "note": n} for i, o, n in INDUSTRIES])
    print(f"divisions={len(DIVISIONS)} areas={len(area_rows)} territories={len(territories)} sellers={len(sellers)} "
          f"accounts={len(accounts)} revenue_rows={len(revenue)} opportunities={len(pipeline)} signals={len(signals)}")


if __name__ == "__main__":
    main()
