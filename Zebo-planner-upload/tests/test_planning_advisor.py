import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from analysis_tools import summarize_plan, find_planning_exceptions, get_territory_detail
from agent_blueprint import execute_read_only_tool, create_change_request

class PlanningAdvisorTests(unittest.TestCase):
    def test_plan_total_mismatch_is_detected(self):
        result = summarize_plan()
        self.assertFalse(result["within_target"])
        self.assertNotEqual(result["quota_minus_target_usd"], 0)

    def test_exceptions_are_returned(self):
        result = find_planning_exceptions()
        self.assertGreater(result["exception_count"], 0)

    def test_known_territory(self):
        result = get_territory_detail("T05")
        self.assertEqual(result["territory_id"], "T05")
        self.assertIn("variance_usd", result)

    def test_unknown_territory_fails_closed(self):
        result = get_territory_detail("BAD")
        self.assertIn("error", result)
        self.assertNotIn("proposed_quota_usd", result)

    def test_unknown_tool_is_rejected(self):
        result = execute_read_only_tool("delete_quota")
        self.assertEqual(result["error"], "Tool not allowlisted")

    def test_change_request_is_draft_only(self):
        result = create_change_request("propose_quota_change", {"territory_id":"T05"})
        self.assertEqual(result["status"], "PENDING_HUMAN_APPROVAL")
        self.assertFalse(result["executed"])

if __name__ == "__main__":
    unittest.main()
