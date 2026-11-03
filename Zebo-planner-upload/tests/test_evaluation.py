"""Evaluation Lab: manual cases are never auto-passed; automated cases really run."""
import unittest

from _helpers import TempDirs

from evaluation import AUTOMATED_CHECKS, MANUAL, load_cases, run_evaluations
from review_store import ReviewStore


class EvaluationLab(unittest.TestCase):
    def test_manual_cases_not_reported_as_passed(self):
        with TempDirs() as t:
            run = run_evaluations(t.data, ReviewStore(t.state))
            manual = [r for r in run["results"] if r["evaluation_type"] == "manual"]
            self.assertEqual({r["test_id"] for r in manual}, {"TC07", "TC08", "TC09", "TC10"})
            for r in manual:
                self.assertEqual(r["status"], MANUAL)
                self.assertIsNone(r["passed"])
                self.assertIsNone(r["ran_at"])
            self.assertEqual(run["summary"]["manual_pending"], 4)

    def test_every_case_without_a_registered_check_is_manual(self):
        for case in load_cases():
            if case["test_id"] not in AUTOMATED_CHECKS:
                self.assertIn(case["test_id"], {"TC07", "TC08", "TC09", "TC10"})

    def test_automated_cases_run_and_pass_on_starter_data(self):
        with TempDirs() as t:
            run = run_evaluations(t.data, ReviewStore(t.state))
            auto = [r for r in run["results"] if r["evaluation_type"] == "automated"]
            self.assertEqual(len(auto), len(AUTOMATED_CHECKS))
            failing = [(r["test_id"], r["actual"]) for r in auto if r["status"] != "Passed"]
            self.assertEqual(failing, [])
            self.assertTrue(all(r["ran_at"] for r in auto))

    def test_automated_check_failure_is_reported_as_failed(self):
        with TempDirs() as t:
            # Make the plan match the target: TC01 expects a mismatch on starter data, so it must now FAIL.
            t.write_quota_plan([["T05", 1000, 1000, 2, 1000, 1.0]])
            run = run_evaluations(t.data, ReviewStore(t.state))
            tc01 = next(r for r in run["results"] if r["test_id"] == "TC01")
            self.assertEqual(tc01["status"], "Failed")
            self.assertGreater(run["summary"]["automated_failed"], 0)

    def test_recorded_manual_result_is_labelled_as_manual(self):
        with TempDirs() as t:
            store = ReviewStore(t.state)
            store.record_manual_evaluation("TC09", "Pass", "Eval lead", "Checked 10 prompts")
            run = run_evaluations(t.data, store)
            tc09 = next(r for r in run["results"] if r["test_id"] == "TC09")
            self.assertEqual(tc09["status"], "Manual: Pass")
            self.assertIn("Eval lead", tc09["actual"])
            with self.assertRaises(ValueError):
                store.record_manual_evaluation("TC07", "Maybe", "x", "")
            with self.assertRaises(ValueError):
                store.record_manual_evaluation("TC07", "Pass", "", "")

    def test_evaluation_does_not_touch_user_review_queue(self):
        with TempDirs() as t:
            store = ReviewStore(t.state)
            run_evaluations(t.data, store)
            self.assertEqual(store.list_requests(), [])


if __name__ == "__main__":
    unittest.main()
