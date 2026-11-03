"""Deterministic planning logic: totals, thresholds, boundaries, validation, formatting."""
import unittest

from _helpers import TempDirs  # noqa: F401  (also puts src/ on sys.path)

from analysis_tools import (DataValidationError, check_data_consistency, evaluate_territory,
                            find_planning_exceptions, get_territory_detail, list_data_gaps, money, pct,
                            preview_quota_scenario, signed_money, source_fingerprint, summarize_plan,
                            validate_all_data)
from config import Thresholds


def row(tid="X1", target=1_000_000, quota=1_000_000, cap=5, hist=1_000_000, opp=1.0):
    return {"territory_id": tid, "company_target_usd": float(target), "proposed_quota_usd": float(quota),
            "rep_capacity": float(cap), "historical_revenue_usd": float(hist), "opportunity_index": float(opp)}


class StarterDataTotals(unittest.TestCase):
    def test_totals_calculated_from_csv(self):
        s = summarize_plan()
        self.assertEqual(s["territory_count"], 10)
        self.assertEqual(s["company_target_usd"], 90_000_000)
        self.assertEqual(s["proposed_quota_total_usd"], 91_175_000)
        self.assertEqual(s["quota_minus_target_usd"], 1_175_000)
        self.assertFalse(s["within_target"])
        self.assertIn("above", s["interpretation"])

    def test_starter_exceptions_are_t05_and_t10_in_priority_order(self):
        e = find_planning_exceptions()
        self.assertEqual([x["territory_id"] for x in e["exceptions"]], ["T05", "T10"])
        self.assertEqual(e["exceptions"][0]["variance_pct"], 8.0)


class MatchingTotals(unittest.TestCase):
    def test_correct_totals_when_target_and_quota_match(self):
        with TempDirs() as t:
            t.write_quota_plan([["A1", 1000, 1000, 2, 1000, 1.0], ["A2", 2500.5, 2500.5, 3, 2000, 1.0]])
            s = summarize_plan(t.data)
            self.assertTrue(s["within_target"])
            self.assertEqual(s["quota_minus_target_usd"], 0)
            self.assertEqual(s["company_target_usd"], 3500.5)
            self.assertEqual(s["interpretation"], "Proposed quota total matches company target.")
            self.assertEqual(find_planning_exceptions(t.data)["exception_count"], 0)

    def test_shortfall_is_reported_as_below(self):
        with TempDirs() as t:
            t.write_quota_plan([["A1", 1000, 900, 2, 1000, 1.0]])
            s = summarize_plan(t.data)
            self.assertEqual(s["quota_minus_target_usd"], -100)
            self.assertIn("below", s["interpretation"])


class ThresholdBoundaries(unittest.TestCase):
    def test_variance_exactly_at_threshold_is_flagged(self):
        self.assertIn("quota_variance", evaluate_territory(row(quota=1_050_000))["flags"])
        self.assertIn("quota_variance", evaluate_territory(row(quota=950_000))["flags"])  # negative side

    def test_variance_just_below_threshold_is_not_flagged(self):
        self.assertNotIn("quota_variance", evaluate_territory(row(quota=1_049_999))["flags"])
        self.assertNotIn("quota_variance", evaluate_territory(row(quota=950_001))["flags"])

    def test_starter_t10_sits_exactly_on_threshold(self):
        t10 = next(x for x in find_planning_exceptions()["exceptions"] if x["territory_id"] == "T10")
        self.assertEqual(t10["variance_pct"], 5.0)

    def test_per_rep_ratio_boundary(self):
        # target high so variance rule is not triggered; ratio = quota/hist when capacity equal
        at = evaluate_territory(row(target=1_500_000, quota=1_500_000, hist=1_000_000))
        below = evaluate_territory(row(target=1_499_000, quota=1_499_000, hist=1_000_000))
        self.assertIn("quota_per_rep_vs_historical", at["flags"])
        self.assertNotIn("quota_per_rep_vs_historical", below["flags"])

    def test_opportunity_index_range_is_inclusive(self):
        for opp, flagged in [(0.5, False), (1.5, False), (0.49, True), (1.51, True)]:
            with self.subTest(opp=opp):
                self.assertEqual("opportunity_index_out_of_range" in evaluate_territory(row(opp=opp))["flags"], flagged)

    def test_thresholds_are_configurable(self):
        e = find_planning_exceptions(thresholds=Thresholds(variance_pct=6.0))
        self.assertEqual([x["territory_id"] for x in e["exceptions"]], ["T05"])
        self.assertEqual(e["thresholds"]["variance_pct"], 6.0)
        self.assertIn("Illustrative", e["threshold_label"])

    def test_invalid_thresholds_rejected(self):
        with self.assertRaises(ValueError):
            Thresholds.from_mapping({"variance_pct": "abc"})
        with self.assertRaises(ValueError):
            Thresholds.from_mapping({"opportunity_index_min": 2, "opportunity_index_max": 1})
        with self.assertRaises(ValueError):
            Thresholds.from_mapping({"variance_pct": 0})


class CapacityAndDivision(unittest.TestCase):
    def test_zero_capacity_is_flagged_without_division_error(self):
        e = evaluate_territory(row(cap=0))
        self.assertIn("invalid_capacity", e["flags"])
        self.assertIsNone(e["quota_per_rep_usd"])
        self.assertIsNone(e["quota_to_hist_per_rep_ratio"])

    def test_negative_capacity_is_flagged(self):
        e = evaluate_territory(row(cap=-3))
        self.assertIn("invalid_capacity", e["flags"])
        self.assertIsNone(e["historical_revenue_per_rep_usd"])

    def test_zero_capacity_in_csv_surfaces_as_exception(self):
        with TempDirs() as t:
            t.write_quota_plan([["A1", 1000, 1000, 0, 1000, 1.0]])
            e = find_planning_exceptions(t.data)
            self.assertEqual(e["exceptions"][0]["flags"], ["invalid_capacity"])
            d = get_territory_detail("A1", data_dir=t.data)
            self.assertIn("Per-rep metrics not calculated", " ".join(d["calculation_steps"]))

    def test_zero_target_avoids_division(self):
        e = evaluate_territory(row(target=0))
        self.assertIsNone(e["variance_pct"])
        self.assertIn("invalid_target", e["flags"])

    def test_zero_historical_revenue_ratio_not_computable(self):
        e = evaluate_territory(row(hist=0))
        self.assertIsNone(e["quota_to_hist_per_rep_ratio"])
        self.assertNotIn("quota_per_rep_vs_historical", e["flags"])


class MalformedInputs(unittest.TestCase):
    def test_missing_csv(self):
        with TempDirs() as t:
            (t.data / "quota_plan.csv").unlink()
            with self.assertRaises(DataValidationError) as cm:
                summarize_plan(t.data)
            self.assertIn("data/quota_plan.csv", str(cm.exception))
            self.assertNotIn(str(t.tmp), str(cm.exception))  # no absolute paths leaked

    def test_missing_columns(self):
        with TempDirs() as t:
            t.write_quota_plan([["A1", 1000, 1000]], header=["territory_id", "company_target_usd", "proposed_quota_usd"])
            with self.assertRaises(DataValidationError) as cm:
                find_planning_exceptions(t.data)
            self.assertIn("missing required column", str(cm.exception))
            self.assertIn("rep_capacity", str(cm.exception))

    def test_invalid_numeric(self):
        with TempDirs() as t:
            t.write_quota_plan([["A1", "twelve million", 1000, 2, 1000, 1.0]])
            with self.assertRaises(DataValidationError) as cm:
                summarize_plan(t.data)
            self.assertIn("row 2", str(cm.exception))
            self.assertIn("company_target_usd", str(cm.exception))

    def test_blank_and_non_finite_numbers(self):
        for bad in ["", "nan", "inf"]:
            with self.subTest(bad=bad), TempDirs() as t:
                t.write_quota_plan([["A1", 1000, bad, 2, 1000, 1.0]])
                with self.assertRaises(DataValidationError):
                    summarize_plan(t.data)

    def test_empty_file_and_header_only(self):
        with TempDirs() as t:
            (t.data / "quota_plan.csv").write_text("", encoding="utf-8")
            with self.assertRaises(DataValidationError) as cm:
                summarize_plan(t.data)
            self.assertIn("empty", str(cm.exception))
            t.write_quota_plan([])
            with self.assertRaises(DataValidationError) as cm:
                summarize_plan(t.data)
            self.assertIn("no data rows", str(cm.exception))

    def test_duplicate_territory_ids(self):
        with TempDirs() as t:
            t.write_quota_plan([["A1", 1, 1, 1, 1, 1], ["A1", 1, 1, 1, 1, 1]])
            with self.assertRaises(DataValidationError):
                summarize_plan(t.data)

    def test_negative_dollar_amount_rejected(self):
        with TempDirs() as t:
            t.write_quota_plan([["A1", 1000, -5, 2, 1000, 1.0]])
            with self.assertRaises(DataValidationError):
                summarize_plan(t.data)

    def test_validate_all_data_reports_instead_of_raising(self):
        with TempDirs() as t:
            (t.data / "accounts.csv").unlink()
            v = validate_all_data(t.data)
            self.assertFalse(v["all_ok"])
            self.assertTrue(v["files"]["data/quota_plan.csv"]["ok"])
            self.assertFalse(v["files"]["data/accounts.csv"]["ok"])

    def test_detail_degrades_when_supplementary_file_missing(self):
        with TempDirs() as t:
            (t.data / "sales_reps.csv").unlink()
            d = get_territory_detail("T05", data_dir=t.data)
            self.assertIsNone(d["roster_summary"])
            self.assertTrue(any("Roster unavailable" in n for n in d["data_notes"]))


class TerritoryLookup(unittest.TestCase):
    def test_known_territory_case_insensitive(self):
        d = get_territory_detail(" t05 ")
        self.assertEqual(d["territory_id"], "T05")
        self.assertEqual(d["variance_usd"], 1_040_000)
        self.assertEqual(d["quota_per_rep_usd"], 1_080_000)
        self.assertEqual(d["territory_name"], "Northeast")
        self.assertEqual(d["roster_summary"]["active_reps"], 13)
        self.assertNotIn("reps", d["roster_summary"])  # aggregate only; no per-rep list

    def test_unknown_territory_fails_closed(self):
        d = get_territory_detail("T99")
        self.assertIn("error", d)
        self.assertNotIn("proposed_quota_usd", d)
        self.assertIn("T05", d["valid_ids"])

    def test_malformed_territory_id_rejected(self):
        for bad in ["../data", "T05; rm -rf /", "", "A" * 40, None, 5]:
            with self.subTest(bad=bad):
                d = get_territory_detail(bad)
                self.assertIn("error", d)
                self.assertNotIn("proposed_quota_usd", d)


class OtherTools(unittest.TestCase):
    def test_consistency_checks_find_roster_reconciliation_notes(self):
        c = check_data_consistency()
        self.assertEqual(c["finding_count"], 10)
        self.assertTrue(all(f["check"] == "roster_revenue_reconciliation" for f in c["findings"]))

    def test_data_gaps_lists_missing_information(self):
        g = list_data_gaps("T05")
        self.assertIn("Prior-year quota and attainment by territory", g["not_in_dataset"])
        self.assertTrue(all(f["territory_id"] == "T05" for f in g["data_quality_findings"]))
        self.assertIn("error", list_data_gaps("T99"))

    def test_scenario_preview_is_hypothetical_and_does_not_write(self):
        before = source_fingerprint()
        p = preview_quota_scenario("T05", 13_650_000)
        self.assertFalse(p["applied"])
        self.assertEqual(p["plan_total_under_alternative_usd"], 90_785_000)
        self.assertEqual(p["plan_gap_under_alternative_usd"], 785_000)
        self.assertEqual(source_fingerprint(), before)
        self.assertEqual(summarize_plan()["proposed_quota_total_usd"], 91_175_000)
        self.assertIn("error", preview_quota_scenario("T05", -1))
        self.assertIn("error", preview_quota_scenario("T05", True))

    def test_formatting_is_consistent(self):
        self.assertEqual(money(1175000), "$1,175,000")
        self.assertEqual(money(-270000), "-$270,000")
        self.assertEqual(signed_money(250000), "+$250,000")
        self.assertEqual(pct(8.0), "+8.00%")
        self.assertEqual(pct(-3), "-3.00%")
        self.assertEqual(money(None), "n/a")


if __name__ == "__main__":
    unittest.main()
