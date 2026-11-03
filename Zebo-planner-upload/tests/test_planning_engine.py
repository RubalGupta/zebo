"""FY27 planning engine: data validation, scope, rollups, draft-plan moves, move advice, purity."""
import hashlib
import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from _helpers import ROOT, TempDirs

HAS_PD = importlib.util.find_spec("pandas") is not None
FY27 = ROOT / "data" / "fy27"


def fp(d=FY27):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(d).glob("*.csv"))}


@unittest.skipUnless(HAS_PD, "pandas required")
class Engine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from planning.data import load
        cls.d = load()

    def setUp(self):
        self.dirs = TempDirs().__enter__()
        from planning.plan_store import PlanStore
        self.store = PlanStore(self.dirs.state)

    def tearDown(self):
        self.dirs.__exit__(None, None, None)

    # ------------------------------------------------------------ dataset shape (as requested)
    def test_dataset_shape(self):
        d = self.d
        self.assertEqual((d.hierarchy["level"] == "division").sum(), 5)
        per_div = d.hierarchy[d.hierarchy["level"] == "area"].groupby("parent_id").size()
        self.assertTrue(((per_div >= 6) & (per_div <= 8)).all())
        self.assertEqual(len(d.accounts), 800)
        self.assertEqual(len(d.sellers), 350)
        self.assertGreaterEqual(d.sellers["role"].nunique(), 6)
        self.assertEqual(d.ytd_months, 11)
        self.assertTrue((d.accounts["data_origin"] == "Synthetic").all())

    def test_rollups_reconcile(self):
        from planning.analysis import summarize
        from planning.plan_store import effective_accounts
        acc = effective_accounts(self.d, None)
        bu = summarize(self.d, acc, "bu", "BU1")
        self.assertAlmostEqual(sum(c["fy26_ytd"] for c in bu["children"]), bu["fy26_ytd"], places=2)
        self.assertAlmostEqual(bu["fy26_ytd"], acc["fy26_ytd"].sum(), places=2)
        area = summarize(self.d, acc, "area", "NAW-A02")
        self.assertAlmostEqual(sum(c["fy26_ytd"] for c in area["children"]), area["fy26_ytd"], places=2)
        # YoY compares like-for-like months (Jan–Nov), not 11 vs 12 months
        f25 = self.d.revenue[(self.d.revenue.fiscal_year == "FY25") & (self.d.revenue.month <= 11)].revenue_usd.sum()
        self.assertAlmostEqual(bu["fy25_ytd"], f25, places=2)

    def test_scope_filters(self):
        from planning.scope import filter_scope, persona_by_id, personas
        ps = personas(self.d)
        self.assertEqual(len(ps), 1 + 5 + 34)
        p = persona_by_id(self.d, "P-NAW-A02")
        sub = filter_scope(p, self.d.accounts)
        self.assertTrue((sub["area_id"] == "NAW-A02").all())
        self.assertGreater(len(sub), 0)

    # ------------------------------------------------------------ plan store
    def test_moves_are_audited_reversible_and_never_touch_source(self):
        from planning.plan_store import effective_accounts
        before = fp()
        with self.assertRaises(ValueError):
            self.store.add_move(account_id="A0091", from_territory_id="NAW-A03-G1", to_territory_id="NAW-A03-D1",
                                from_owner_id="x", to_owner_id="y", reason="  ", verdict="v", score=0, by="t")
        mv = self.store.add_move(account_id="A0091", from_territory_id="NAW-A03-G1", to_territory_id="NAW-A03-D1",
                                 from_owner_id="x", to_owner_id="y", reason="purity", verdict="Review with caution",
                                 score=1, by="tester")
        acc = effective_accounts(self.d, self.store)
        row = acc[acc.account_id == "A0091"].iloc[0]
        self.assertEqual(row.territory_id, "NAW-A03-D1")
        self.assertTrue(row.moved)
        self.assertEqual(row.fy26_territory_id, "NAW-A03-G1")
        self.store.revert(mv["move_id"], "tester")
        acc = effective_accounts(self.d, self.store)
        self.assertEqual(acc[acc.account_id == "A0091"].iloc[0].territory_id, "NAW-A03-G1")
        with self.assertRaises(ValueError):
            self.store.revert(mv["move_id"], "tester")
        self.assertEqual(fp(), before)
        self.assertEqual(len(self.store.moves(active_only=False)), 1)

    def test_revert_order_enforced(self):
        m1 = self.store.add_move(account_id="A0091", from_territory_id="NAW-A03-G1", to_territory_id="NAW-A03-D1",
                                 from_owner_id="x", to_owner_id="y", reason="r1", verdict="v", score=0, by="t")
        self.store.add_move(account_id="A0091", from_territory_id="NAW-A03-D1", to_territory_id="NAW-A03-E1",
                            from_owner_id="y", to_owner_id="z", reason="r2", verdict="v", score=0, by="t")
        with self.assertRaises(ValueError):
            self.store.revert(m1["move_id"], "t")

    # ------------------------------------------------------------ advice
    def test_move_advice_factors(self):
        from planning.advice import evaluate_move
        from planning.plan_store import effective_accounts
        acc = effective_accounts(self.d, None)
        ev = evaluate_move(self.d, acc, "A0091", "NAW-A03-D1")
        factors = {r["factor"] for r in ev["reasons"]}
        self.assertTrue({"Motion fit", "Purity", "Pipeline continuity", "Capacity"} <= factors)
        self.assertEqual(ev["score"], sum(r["impact"] for r in ev["reasons"]))
        bad = evaluate_move(self.d, acc, "A0091", "NAW-A03-G2" if "NAW-A03-G2" in set(self.d.territories.territory_id) else "NAW-A03-E1")
        self.assertIn("purity break", " ".join(r["finding"] for r in bad["reasons"]))
        self.assertLess(bad["score"], ev["score"])
        self.assertIn("error", evaluate_move(self.d, acc, "A0091", "NAW-A03-G1"))   # already there
        self.assertIn("error", evaluate_move(self.d, acc, "A9999", "NAW-A03-D1"))
        cross = evaluate_move(self.d, acc, "A0091", "NAW-A02-D1")
        self.assertIn("Approval", {r["factor"] for r in cross["reasons"]})

    def test_verdict_thresholds(self):
        from planning.advice import evaluate_move
        from planning.plan_store import effective_accounts
        acc = effective_accounts(self.d, None)
        for tid in self.d.territories.territory_id[:40]:
            ev = evaluate_move(self.d, acc, "A0091", tid)
            if "error" in ev:
                continue
            want = "Recommended" if ev["score"] >= 2 else "Not recommended" if ev["score"] <= -2 else "Review with caution"
            self.assertEqual(ev["verdict"], want)

    def test_purity_finds_seeded_misfits_and_fixes_match_motion(self):
        from planning.advice import purity_fix, purity_report
        from planning.plan_store import effective_accounts
        acc = effective_accounts(self.d, None)
        rep = purity_report(self.d, acc, self.d.territories)
        self.assertEqual(len(rep["misfits"]), 62)    # seeded by tools/generate_fy27_data.py
        self.assertGreater(rep["critical"], 0)
        for m in rep["misfits"][:10]:
            f = purity_fix(self.d, acc, m)
            if f:
                self.assertEqual(f["to_motion"], m["account_motion"])

    def test_malformed_fy27_data(self):
        from planning.data import PlanningDataError, load
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / "fy27"
            shutil.copytree(FY27, dst)
            (dst / "pipeline.csv").write_text("opportunity_id,account_id\nO1,A0001\n", encoding="utf-8")
            with self.assertRaises(PlanningDataError) as cm:
                load(dst)
            self.assertIn("missing column", str(cm.exception))
            shutil.copy(FY27 / "pipeline.csv", dst / "pipeline.csv")
            (dst / "accounts.csv").unlink()
            with self.assertRaises(PlanningDataError):
                load(dst)


if __name__ == "__main__":
    unittest.main()
