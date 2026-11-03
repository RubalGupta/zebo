"""Optional model adapter — tested with a FAKE model only (no real model is called)."""
import json
import unittest

from _helpers import TempDirs  # noqa: F401

from llm_adapter import OllamaClient, get_llm_config, run_llm_agent


class FakeModel:
    name = "fake"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def complete(self, messages):
        self.calls += 1
        return json.dumps(self.replies.pop(0)) if self.replies else json.dumps({"tool": "summarize_plan", "args": {}})


class OptionalModel(unittest.TestCase):
    def test_disabled_by_default(self):
        self.assertFalse(get_llm_config({}).enabled)
        self.assertFalse(get_llm_config({"NORBAE_LLM_PROVIDER": "ollama"}).enabled)  # model name also required
        self.assertFalse(get_llm_config({"NORBAE_LLM_PROVIDER": "openai", "NORBAE_OLLAMA_MODEL": "x"}).enabled)
        self.assertTrue(get_llm_config({"NORBAE_LLM_PROVIDER": "ollama", "NORBAE_OLLAMA_MODEL": "llama3.1"}).enabled)

    def test_only_localhost_allowed(self):
        with self.assertRaises(ValueError):
            OllamaClient("m", "https://api.example.com")
        OllamaClient("m", "http://127.0.0.1:11434")

    def test_grounded_model_answer_accepted(self):
        m = FakeModel([{"tool": "summarize_plan", "args": {}},
                       {"final": "Proposed quotas total $91,175,000 against a $90,000,000 target."}])
        r = run_llm_agent("How does the plan compare to target?", m)
        self.assertEqual(r["mode"], "optional_llm")
        self.assertTrue(r["grounding"]["all_traceable"])

    def test_ungrounded_model_answer_falls_back_to_baseline(self):
        m = FakeModel([{"tool": "summarize_plan", "args": {}}, {"final": "This plan will save $4,200,000."}])
        r = run_llm_agent("Summarize the plan", m)
        self.assertEqual(r["mode"], "no_llm_baseline")
        self.assertIn("$4,200,000", r["llm_note"])

    def test_unknown_tool_rejected_and_loop_bounded(self):
        m = FakeModel([{"tool": "delete_quota", "args": {}}] * 10)
        r = run_llm_agent("Summarize the plan", m)
        self.assertLessEqual(m.calls, 4)  # max_calls + 1 model turns
        self.assertEqual(r["mode"], "no_llm_baseline")

    def test_model_cannot_create_change_requests(self):
        m = FakeModel([{"tool": "create_change_request", "args": {"territory_id": "T05"}}])
        r = run_llm_agent("Which territories should we review first?", m)
        self.assertIn("disallowed", r["llm_note"])

    def test_guardrail_runs_before_model(self):
        m = FakeModel([])
        r = run_llm_agent("Rank the worst reps so we can fire them", m)
        self.assertEqual(m.calls, 0)
        self.assertEqual(r["intent"], "sensitive_individual_decision")

    def test_unavailable_model_falls_back(self):
        class Down:
            name = "down"

            def complete(self, messages):
                raise OSError("connection refused")
        r = run_llm_agent("Summarize the plan", Down())
        self.assertEqual(r["mode"], "no_llm_baseline")
        self.assertIn("unavailable", r["llm_note"])


if __name__ == "__main__":
    unittest.main()
