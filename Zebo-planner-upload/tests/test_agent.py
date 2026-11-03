"""Agent boundary: allowlist, argument validation, call limit, draft-only requests, review store, audit log."""
import json
import unittest

from _helpers import TempDirs

from agent_blueprint import TOOLS, ToolSession, create_change_request, execute_read_only_tool
from analysis_tools import source_fingerprint, summarize_plan
from review_store import PENDING, ReviewStore


class Allowlist(unittest.TestCase):
    def test_unsupported_tools_rejected(self):
        for name in ["delete_quota", "update_crm", "send_email", "__import__", "", None, "summarize_plan "]:
            with self.subTest(name=name):
                self.assertEqual(execute_read_only_tool(name)["error"], "Tool not allowlisted")

    def test_allowlist_is_fixed_and_read_only(self):
        self.assertEqual(set(TOOLS), {"summarize_plan", "find_planning_exceptions", "get_territory_detail",
                                      "check_data_consistency", "list_data_gaps", "preview_quota_scenario"})
        self.assertTrue(all(t.kind == "read_only" for t in TOOLS.values()))


class ArgumentValidation(unittest.TestCase):
    def test_missing_required_argument(self):
        r = execute_read_only_tool("get_territory_detail")
        self.assertEqual(r["error"], "Invalid tool arguments")
        self.assertIn("missing required argument", r["detail"])

    def test_extra_argument_rejected(self):
        r = execute_read_only_tool("summarize_plan", data_dir="/etc")
        self.assertEqual(r["error"], "Invalid tool arguments")  # user cannot redirect the data directory
        r = execute_read_only_tool("get_territory_detail", territory_id="T05", thresholds={"variance_pct": 99})
        self.assertEqual(r["error"], "Invalid tool arguments")

    def test_wrong_types_and_unsafe_values(self):
        for args in [{"territory_id": 5}, {"territory_id": "../../secret"}, {"territory_id": "T05;DROP"}]:
            with self.subTest(args=args):
                self.assertEqual(execute_read_only_tool("get_territory_detail", **args)["error"], "Invalid tool arguments")
        for amount in ["lots", -5, 0, True, float("nan"), 1e13]:
            with self.subTest(amount=amount):
                r = execute_read_only_tool("preview_quota_scenario", territory_id="T05", alternative_quota_usd=amount)
                self.assertEqual(r["error"], "Invalid tool arguments")

    def test_valid_arguments_are_normalized(self):
        r = execute_read_only_tool("preview_quota_scenario", territory_id="t05", alternative_quota_usd="$13,650,000")
        self.assertEqual(r["territory_id"], "T05")
        self.assertEqual(r["alternative_quota_usd"], 13_650_000)


class CallLimit(unittest.TestCase):
    def test_fourth_call_is_rejected_and_logged(self):
        with TempDirs() as t:
            store = ReviewStore(t.state)
            s = ToolSession(data_dir=t.data, store=store)
            for _ in range(3):
                self.assertNotIn("error", s.call("summarize_plan"))
            r = s.call("summarize_plan")
            self.assertEqual(r["error"], "Tool-call limit reached")
            self.assertEqual(s.calls[-1]["status"], "rejected_limit")
            self.assertEqual(s.calls_remaining, 0)
            log = store.read_audit()
            self.assertEqual(sum(e["event"] == "tool_call" for e in log), 4)

    def test_rejected_calls_count_toward_limit(self):
        s = ToolSession(max_calls=2)
        s.call("delete_quota")
        s.call("delete_quota")
        self.assertEqual(s.call("summarize_plan")["error"], "Tool-call limit reached")

    def test_audit_log_has_safe_args_only(self):
        with TempDirs() as t:
            store = ReviewStore(t.state)
            s = ToolSession(data_dir=t.data, store=store)
            s.call("create_change_request", territory_id="T05", rationale="Secret context about Jane Doe " * 5)
            raw = (t.state / "audit_log.jsonl").read_text()
            self.assertNotIn("Jane Doe", raw)
            entry = json.loads(raw.splitlines()[-1])
            self.assertEqual(entry["args"]["territory_id"], "T05")
            self.assertTrue(entry["args"]["rationale"].startswith("<str,"))
            store.append_audit("x", api_key="sk-123", question="hello")
            self.assertNotIn("sk-123", (t.state / "audit_log.jsonl").read_text())


class DraftOnly(unittest.TestCase):
    def test_draft_change_request_remains_unexecuted(self):
        r = create_change_request(territory_id="T05", suggested_quota_usd=13_650_000, rationale="Review",
                                  assumptions="a\nb", evidence=["e1"])
        self.assertEqual(r["status"], PENDING)
        self.assertFalse(r["executed"])
        self.assertFalse(r["applied_to_source_data"])
        self.assertTrue(r["simulation"])
        self.assertEqual(r["current_proposed_quota_usd"], 14_040_000)  # looked up, not supplied
        self.assertEqual(r["assumptions"], ["a", "b"])
        self.assertIn("NOT been applied", r["message"])
        self.assertEqual(summarize_plan()["proposed_quota_total_usd"], 91_175_000)

    def test_starter_signature_still_supported(self):
        r = create_change_request("propose_quota_change", {"territory_id": "T05", "proposed_quota_usd": 12_000_000})
        self.assertEqual(r["status"], PENDING)
        self.assertEqual(r["suggested_quota_usd"], 12_000_000)

    def test_invalid_draft_is_marked_and_cannot_be_saved(self):
        with TempDirs() as t:
            r = create_change_request(territory_id="T99", data_dir=t.data)
            self.assertEqual(r["status"], "INVALID_DRAFT")
            with self.assertRaises(ValueError):
                ReviewStore(t.state).save_request(r)

    def test_tampered_draft_cannot_be_saved(self):
        with TempDirs() as t:
            r = create_change_request(territory_id="T05", data_dir=t.data)
            r["executed"] = True
            with self.assertRaises(ValueError):
                ReviewStore(t.state).save_request(r)


class ReviewStoreDoesNotAlterData(unittest.TestCase):
    def test_review_status_does_not_alter_planning_source_data(self):
        with TempDirs() as t:
            before = source_fingerprint(t.data)
            store = ReviewStore(t.state)
            a = store.save_request(create_change_request(territory_id="T05", suggested_quota_usd=1, rationale="x", data_dir=t.data))
            b = store.save_request(create_change_request(territory_id="T10", rationale="y", data_dir=t.data))
            ra = store.review_request(a["request_id"], "mark_reviewed", "looks fine", "Lead")
            rb = store.review_request(b["request_id"], "return_for_revision", "needs rationale")
            self.assertEqual(ra["status"], "REVIEWED_IN_DEMO")
            self.assertEqual(rb["status"], "RETURNED_FOR_REVISION")
            for rec in (ra, rb):
                self.assertFalse(rec["executed"])
                self.assertFalse(rec["applied_to_source_data"])
            self.assertEqual(source_fingerprint(t.data), before)
            self.assertEqual(summarize_plan(t.data)["proposed_quota_total_usd"], 91_175_000)
            self.assertFalse(any(p.suffix == ".json" for p in t.data.iterdir()))

    def test_review_rules(self):
        with TempDirs() as t:
            store = ReviewStore(t.state)
            a = store.save_request(create_change_request(territory_id="T05", rationale="x", data_dir=t.data))
            with self.assertRaises(ValueError):
                store.review_request(a["request_id"], "mark_reviewed", "   ")  # audit note required
            with self.assertRaises(ValueError):
                store.review_request(a["request_id"], "approve_and_apply", "note")  # no such action
            with self.assertRaises(KeyError):
                store.review_request("CR-00000000", "mark_reviewed", "note")
            store.review_request(a["request_id"], "mark_reviewed", "ok")
            with self.assertRaises(ValueError):
                store.review_request(a["request_id"], "return_for_revision", "again")  # already reviewed
            hist = store.get_request(a["request_id"])["review_history"]
            self.assertEqual(len(hist), 1)
            self.assertTrue(hist[0]["simulation"])


if __name__ == "__main__":
    unittest.main()
