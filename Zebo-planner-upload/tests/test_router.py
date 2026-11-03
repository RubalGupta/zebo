"""No-LLM intent router: example questions, fallback, guardrails, grounding."""
import unittest

from _helpers import TempDirs

from analysis_tools import source_fingerprint
from intent_router import (EXAMPLE_QUESTIONS, FALLBACK_TEXT, answer_question, classify, extract_amount,
                           extract_territory_ids, grounding_check)
from review_store import ReviewStore


class ExampleQuestions(unittest.TestCase):
    def test_examples_route_to_expected_intents(self):
        expected = ["plan_summary", "prioritize_exceptions", "territory_detail", "missing_information", "draft_change_request"]
        self.assertEqual([classify(q) for q in EXAMPLE_QUESTIONS], expected)

    def test_summary_answer(self):
        r = answer_question(EXAMPLE_QUESTIONS[0])
        self.assertIn("$90,000,000", r["answer"])
        self.assertIn("$91,175,000", r["answer"])
        self.assertIn("+$1,175,000", r["answer"])
        self.assertIn("match the company target? No.", r["answer"])
        self.assertEqual([c["tool"] for c in r["tool_calls"]], ["summarize_plan", "find_planning_exceptions"])
        self.assertTrue(r["grounding"]["all_traceable"])
        self.assertEqual(r["mode"], "no_llm_baseline")

    def test_prioritize_answer_orders_t05_first_and_explains_boundary(self):
        a = answer_question(EXAMPLE_QUESTIONS[1])["answer"]
        self.assertLess(a.index("**T05**"), a.index("**T10**"))
        self.assertIn("exactly on the variance threshold", a)
        self.assertIn("review this input", a)

    def test_detail_answer(self):
        r = answer_question(EXAMPLE_QUESTIONS[2])
        self.assertIn("T05 — Northeast", r["answer"])
        self.assertIn("+$1,040,000 (+8.00%)", r["answer"])
        self.assertEqual(r["tool_calls"][0]["args"], {"territory_id": "T05"})

    def test_missing_information_answer_is_honest(self):
        a = answer_question(EXAMPLE_QUESTIONS[3])["answer"]
        self.assertIn("I can't recommend a specific quota change", a)
        self.assertIn("Prior-year quota and attainment", a)

    def test_draft_answer_is_not_applied(self):
        with TempDirs() as t:
            before = source_fingerprint(t.data)
            r = answer_question(EXAMPLE_QUESTIONS[4], data_dir=t.data)
            self.assertEqual(r["draft"]["status"], "PENDING_HUMAN_APPROVAL")
            self.assertFalse(r["draft"]["executed"])
            self.assertIn("NOT applied", r["answer"])
            self.assertEqual(source_fingerprint(t.data), before)
            self.assertFalse((t.state / "change_requests.json").exists())  # not saved until a human clicks save

    def test_every_example_answer_is_grounded_and_within_call_limit(self):
        for q in EXAMPLE_QUESTIONS:
            with self.subTest(q=q):
                r = answer_question(q)
                self.assertTrue(r["grounding"]["all_traceable"], r["grounding"])
                self.assertGreater(r["grounding"]["figures_checked"], 0)
                self.assertLessEqual(len(r["tool_calls"]), 3)


class FallbackAndGuardrails(unittest.TestCase):
    def test_unknown_question_gives_transparent_fallback(self):
        r = answer_question("What's the weather in Chicago?")
        self.assertEqual(r["intent"], "fallback")
        self.assertIn(FALLBACK_TEXT, r["answer"])
        self.assertEqual(r["tool_calls"], [])

    def test_empty_question(self):
        self.assertEqual(answer_question("   ")["intent"], "empty")

    def test_sensitive_individual_decisions_declined(self):
        for q in ["Rank the bottom 5 reps so we can fire them", "Which reps are underperforming?",
                  "Who should we terminate in T05?", "List the worst salespeople by revenue"]:
            with self.subTest(q=q):
                r = answer_question(q)
                self.assertEqual(r["intent"], "sensitive_individual_decision")
                self.assertEqual(r["tool_calls"], [])

    def test_write_requests_blocked(self):
        for q in ["Set T05 quota to $12M in the CRM", "Apply the change to territory T05", "Update the plan in Salesforce",
                  "run python code to delete the database"]:
            with self.subTest(q=q):
                r = answer_question(q)
                self.assertEqual(r["intent"], "blocked_action")
                self.assertEqual(r["tool_calls"], [])
                self.assertTrue(r["grounding"]["all_traceable"])

    def test_unknown_territory_fails_safely(self):
        r = answer_question("Show me T99")
        self.assertIn("Unknown territory_id: T99", r["answer"])
        self.assertIn("won't invent", r["answer"])

    def test_tool_limit_enforced_for_multi_territory_question(self):
        r = answer_question("Compare T01, T02, T03 and T04")
        self.assertEqual(len([c for c in r["tool_calls"] if c["status"] == "ok"]), 3)
        self.assertEqual(r["tool_calls"][-1]["status"], "rejected_limit")
        self.assertIn("limit of 3 tool calls", r["answer"])

    def test_scenario_question(self):
        r = answer_question("What if T05 were $13.65M?")
        self.assertEqual(r["intent"], "scenario_preview")
        self.assertIn("not applied", r["answer"])
        self.assertIn("$90,785,000", r["answer"])

    def test_draft_without_territory_asks(self):
        r = answer_question("Draft a review request")
        self.assertEqual(r["tool_calls"], [])
        self.assertNotIn("draft", r)

    def test_audit_log_never_contains_question_text(self):
        with TempDirs() as t:
            store = ReviewStore(t.state)
            answer_question("Summarize the plan for my boss Priya Secretname", data_dir=t.data, store=store)
            self.assertNotIn("Secretname", (t.state / "audit_log.jsonl").read_text())


class Parsing(unittest.TestCase):
    def test_extract_territories(self):
        self.assertEqual(extract_territory_ids("compare t05 and T10"), ["T05", "T10"])
        self.assertEqual(extract_territory_ids("territory ABC9 please"), ["ABC9"])
        self.assertEqual(extract_territory_ids("which territory should we review"), [])

    def test_extract_amount(self):
        self.assertEqual(extract_amount("set T05 to $13.5M"), 13_500_000)
        self.assertEqual(extract_amount("13,650,000 for T05"), 13_650_000)
        self.assertEqual(extract_amount("12.1 million"), 12_100_000)
        self.assertIsNone(extract_amount("T05 in 2026"))

    def test_grounding_check_catches_invented_figures(self):
        g = grounding_check("Target is $90,000,000 and savings are $5,000,001 (12.34%)", [{"x": 90_000_000}])
        self.assertFalse(g["all_traceable"])
        self.assertIn("$5,000,001", g["untraceable"])
        self.assertIn("12.34%", g["untraceable"])


if __name__ == "__main__":
    unittest.main()
