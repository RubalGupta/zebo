"""FY27 Advisor: conversation intents, scope enforcement, tool boundary, grounding, optional Claude engine."""
import importlib.util
import json
import os
import types
import unittest

from _helpers import TempDirs

HAS_PD = importlib.util.find_spec("pandas") is not None


@unittest.skipUnless(HAS_PD, "pandas required")
class AdvisorBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from planning.data import load
        cls.d = load()

    def setUp(self):
        from advisor.rules_engine import RuleAdvisor
        from advisor.tools import PlanningToolkit
        from planning.plan_store import PlanStore
        from planning.scope import persona_by_id
        self.dirs = TempDirs().__enter__()
        self.store = PlanStore(self.dirs.state)
        self.persona = persona_by_id(self.d, "P-NAW")
        self.tk = lambda: PlanningToolkit(self.d, self.store, self.persona)
        self.adv = RuleAdvisor(self.tk)
        self.ctx = {}

    def tearDown(self):
        self.dirs.__exit__(None, None, None)

    def ask(self, q):
        return self.adv.respond(q, self.ctx)


class Conversation(AdvisorBase):
    def test_briefing_is_scoped_and_grounded(self):
        r = self.ask("brief me")
        self.assertIn("North America West", r["text"])
        self.assertIn("Jan–Nov", r["text"])
        self.assertTrue(r["grounding"]["all_traceable"])
        self.assertGreater(r["grounding"]["figures_checked"], 3)

    def test_drilldown_context_carries_to_follow_ups(self):
        self.ask("drill into Pacific Northwest")
        self.assertEqual(self.ctx["focus"], ("area", "NAW-A02"))
        r = self.ask("which accounts are declining?")
        self.assertIn("Pacific Northwest", r["text"])
        self.assertEqual(r["tool_calls"][0]["args"]["within_id"], "NAW-A02")
        rows = self.tk().call("rank", level="account", metric="yoy_pct", order="asc", within_id="NAW-A02")["rows"]
        yoys = [x["yoy_pct"] for x in rows]
        self.assertEqual(yoys, sorted(yoys))
        self.assertIn(rows[0]["name"], r["text"])
        r = self.ask("go up")
        self.assertEqual(self.ctx["focus"], ("division", "NAW"))

    def test_entity_drill_account_and_territory(self):
        r = self.ask("show me Selridge Energy")
        self.assertEqual(r["focus"], ("account", "A0091"))
        self.assertIn("Motion mismatch", r["text"])
        r = self.ask("NAW-A03-D1")
        self.assertEqual(r["focus"], ("territory", "NAW-A03-D1"))

    def test_move_returns_proposal_not_change(self):
        r = self.ask("Move Selridge Energy to Southern California Deep 1")
        self.assertEqual(len(r["proposals"]), 1)
        self.assertEqual(r["proposals"][0]["to_territory_id"], "NAW-A03-D1")
        self.assertIn("Advice only", r["text"])
        self.assertEqual(self.store.moves(), [])
        self.assertTrue(r["grounding"]["all_traceable"])

    def test_move_to_seller_resolves_their_territory(self):
        owner = self.d.territories.set_index("territory_id").loc["NAW-A03-D1", "owner_id"]
        name = self.d.sellers.set_index("seller_id").loc[owner, "seller_name"]
        r = self.ask(f"what if Selridge Energy goes to {name}")
        self.assertEqual(r["proposals"][0]["to_territory_id"], "NAW-A03-D1")

    def test_where_should_and_purity(self):
        r = self.ask("Where should Selridge Energy go?")
        self.assertTrue(1 <= len(r["proposals"]) <= 3)
        r = self.ask("are my territories pure?")
        self.assertIn("Territory purity — North America West", r["text"])
        self.assertTrue(r["proposals"])
        for p in r["proposals"]:
            self.assertEqual(p["to_motion"], p["account_motion"])

    def test_scope_enforced(self):
        r = self.ask("show me EMEA")
        self.assertIn("outside your access", r["text"])
        tk = self.tk()
        self.assertIn("outside your access", tk.call("drill", entity_id="EMEA-A02")["error"])
        self.assertIn("error", tk.call("evaluate_move", account_id="A0091", to_territory_id="EMEA-A02-D1"))

    def test_guardrails_and_later_phases(self):
        r = self.ask("rank the worst reps so we can fire them")
        self.assertEqual(r["tool_calls"], [])
        self.assertIn("can't rank individual sellers", r["text"])
        r = self.ask("update the CRM with these owners")
        self.assertIn("can't run code or write to the CRM", r["text"])
        self.assertIn("Phase 2", self.ask("how do we estimate December revenue?")["text"])
        self.assertIn("Phase 3", self.ask("what is my quota?")["text"])

    def test_fallback_is_honest(self):
        r = self.ask("zzz qqq")
        self.assertEqual(r["tool_calls"], [])
        self.assertIn("haven't looked anything up", r["text"])

    def test_changes_listing(self):
        self.store.add_move(account_id="A0091", from_territory_id="NAW-A03-G1", to_territory_id="NAW-A03-D1",
                            from_owner_id="x", to_owner_id="y", reason="purity", verdict="v", score=0, by="t · x")
        self.assertIn("1 move(s)", self.ask("show my changes")["text"])


class ToolBoundary(AdvisorBase):
    def test_allowlist_validation_and_limit(self):
        tk = self.tk()
        self.assertEqual(tk.call("apply_move", account_id="A0091")["error"], "Tool not allowlisted")
        self.assertIn("Invalid tool arguments", tk.call("drill")["error"])
        self.assertIn("Invalid tool arguments", tk.call("drill", entity_id="../etc")["error"])
        self.assertIn("Invalid tool arguments", tk.call("rank", level="galaxy", metric="fy26_ytd")["error"])
        self.assertIn("Invalid tool arguments", tk.call("scope_overview", data_dir="/tmp")["error"])
        tk.call("scope_overview")
        self.assertEqual(tk.call("scope_overview")["error"], "Tool-call limit reached")
        self.assertEqual(len(tk.calls), 7)

    def test_schemas_exported_for_model_engines(self):
        names = {s["name"] for s in self.tk().json_schemas()}
        self.assertIn("evaluate_move", names)
        self.assertNotIn("apply_move", names)


class Grounding(unittest.TestCase):
    def test_rounding_aware(self):
        from advisor import grounding
        res = [{"v": 104_712_345.0, "p": 6.93}]
        self.assertTrue(grounding.check("Revenue $104.7M, +6.9%", res)["all_traceable"])
        self.assertFalse(grounding.check("Revenue $105.7M", res)["all_traceable"])
        self.assertFalse(grounding.check("growth of 12.0%", res)["all_traceable"])


# ---------------------------------------------------------------- Claude engine (fake client only)
def _block(**k):
    return types.SimpleNamespace(**k)


class FakeClient:
    def __init__(self, script):
        self.script, self.calls = list(script), 0
        self.messages = types.SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls += 1
        assert "tools" in kwargs and kwargs["model"]
        step = self.script.pop(0) if self.script else ("tool", "scope_overview", {})
        if step[0] == "tool":
            return types.SimpleNamespace(stop_reason="tool_use", content=[
                _block(type="tool_use", id=f"t{self.calls}", name=step[1], input=step[2])])
        return types.SimpleNamespace(stop_reason="end_turn", content=[_block(type="text", text=step[1])])


class ClaudeEngine(AdvisorBase):
    def make(self, script):
        from advisor.claude_engine import ClaudeAdvisor
        return ClaudeAdvisor(self.tk, client=FakeClient(script), model="test-model")

    def test_grounded_answer_accepted_and_proposals_surface(self):
        tk = self.tk()
        ev = tk.call("evaluate_move", account_id="A0091", to_territory_id="NAW-A03-D1")
        from planning.analysis import fmt_money
        adv = self.make([("tool", "evaluate_move", {"account_id": "A0091", "to_territory_id": "NAW-A03-D1"}),
                         ("text", f"Verdict: {ev['verdict']}. Revenue {fmt_money(ev['fy26_ytd'])} Jan–Nov.")])
        r = adv.respond("should Selridge move to Southern California Deep 1?", {})
        self.assertEqual(r["engine"], "Claude API (test-model)")
        self.assertEqual(len(r["proposals"]), 1)
        self.assertTrue(r["grounding"]["all_traceable"])

    def test_ungrounded_answer_falls_back(self):
        adv = self.make([("tool", "scope_overview", {}), ("text", "Revenue is $999.9M and growing 42.0%")])
        r = adv.respond("brief me", {})
        from advisor.rules_engine import ENGINE_NAME
        self.assertEqual(r["engine"], ENGINE_NAME)
        self.assertNotIn("$999.9M", r["text"])
        self.assertIn("rule-based answer", r["engine_note"])

    def test_loop_is_bounded_and_unknown_tools_rejected(self):
        client = FakeClient([("tool", "apply_move", {})] * 20)
        from advisor.claude_engine import ClaudeAdvisor
        adv = ClaudeAdvisor(self.tk, client=client, model="m")
        r = adv.respond("brief me", {})
        self.assertLessEqual(client.calls, 6)
        self.assertIn("rule-based", r["engine_note"])

    def test_guardrail_before_model(self):
        client = FakeClient([])
        from advisor.claude_engine import ClaudeAdvisor
        r = ClaudeAdvisor(self.tk, client=client, model="m").respond("rank the bottom reps to fire", {})
        self.assertEqual(client.calls, 0)
        self.assertIn("guardrail", r["engine_note"])

    def test_factory_defaults_to_rules(self):
        from advisor.claude_engine import get_advisor
        old = {k: os.environ.pop(k, None) for k in ("NORBAE_ADVISOR_ENGINE", "ANTHROPIC_API_KEY")}
        try:
            self.assertEqual(type(get_advisor(self.tk)).__name__, "RuleAdvisor")
            os.environ["NORBAE_ADVISOR_ENGINE"] = "claude"     # no key -> still rules
            self.assertEqual(type(get_advisor(self.tk)).__name__, "RuleAdvisor")
        finally:
            os.environ.pop("NORBAE_ADVISOR_ENGINE", None)
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()


class RevenueImpact(AdvisorBase):
    def test_users_exact_follow_up_after_purity(self):
        """Regression: 'if i make the change how will the revenue be impacted for 2027' after purity suggestions."""
        self.ask("are my territories pure?")
        r = self.ask("if i make the change how will the revenue be impacted for 2027")
        self.assertEqual(r["tool_calls"][0]["tool"], "revenue_impact")
        self.assertIn("Estimated FY27 revenue impact of the moves I just suggested", r["text"])
        self.assertTrue(r["grounding"]["all_traceable"], r["grounding"])
        self.assertEqual(self.store.moves(), [])        # estimating never changes the plan

    def test_named_move_and_plan_and_empty(self):
        r = self.ask("what is the revenue impact of moving Selridge Energy to Southern California Deep 1")
        self.assertIn("the move you described", r["text"])
        self.assertIn("nothing to estimate", self.ask("what will my draft plan do to fy27 revenue")["text"])
        self.store.add_move(account_id="A0091", from_territory_id="NAW-A03-G1", to_territory_id="NAW-A03-D1",
                            from_owner_id="x", to_owner_id="y", reason="purity", verdict="v", score=0, by="t · x")
        r = self.ask("what will my draft plan do to fy27 revenue")
        self.assertIn("your draft FY27 plan", r["text"])
        self.assertTrue(r["grounding"]["all_traceable"])

    def test_impact_math_nets_out_and_is_scope_checked(self):
        from planning.impact import moves_impact
        from planning.plan_store import effective_accounts
        acc = effective_accounts(self.d, None)
        r = moves_impact(self.d, acc, [("A0091", "NAW-A03-D1")])
        i = r["items"][0]
        shifts = {t["territory_id"]: t["fy27_change"] for t in r["territory_shifts"]}
        self.assertAlmostEqual(shifts["NAW-A03-D1"] + shifts["NAW-A03-G1"], i["company_delta"], places=2)
        self.assertLessEqual(i["company_delta_low"], i["company_delta"])
        self.assertLessEqual(i["company_delta"], i["company_delta_high"])
        self.assertIn("error", self.tk().call("revenue_impact", account_ids=["A0091"], to_territory_ids=["EMEA-A02-D1"]))
        self.assertIn("Invalid tool arguments", self.tk().call("revenue_impact", account_ids=["A0091"],
                                                              to_territory_ids=["NAW-A03-D1", "NAW-A03-E1"])["error"])


@unittest.skipUnless(HAS_PD, "pandas required")
class FollowUpConversation(AdvisorBase):
    """The VP demo flow: declining area -> who owns it -> his book -> pipeline -> move -> new territory purity -> should I?"""

    def setUp(self):
        super().setUp()
        from planning.scope import persona_by_id
        self.persona = persona_by_id(self.d, "P-BU1")

    def ask(self, q):
        return self.adv.respond(q, self.ctx)

    def test_vp_demo_flow(self):
        r = self.ask("Which areas are declining?")
        self.assertIn("Greater China", r["text"].split("\n")[1])
        r = self.ask("Who owns the top declining?")
        self.assertEqual(r["tool_calls"][0]["tool"], "decline_drivers")
        self.assertIn("Taylor Marlowe", r["text"])
        r = self.ask("Show me his entire book")
        self.assertEqual(r["tool_calls"][0]["tool"], "seller_book")
        self.assertIn("Over capacity", r["text"])
        self.assertIn("Mardex Utilities (Expansion)", r["text"])
        r = self.ask("What's the opportunity pipeline for it?")
        self.assertEqual(r["tool_calls"][0]["tool"], "pipeline_summary")
        self.assertIn("Taylor Marlowe's book", r["text"])
        r = self.ask("Move Mardex Utilities to Dana Pemberton")
        ev = r["proposals"][0]
        self.assertEqual(ev["to_territory_id"], "APAC-A03-E1")
        self.assertEqual(self.store.moves(), [])                     # advice only
        self.store.add_move(account_id=ev["account_id"], from_territory_id=ev["from_territory_id"],
                            to_territory_id=ev["to_territory_id"], from_owner_id=ev["from_owner_id"],
                            to_owner_id=ev["to_owner_id"], reason="Expansion account to an Expansion AE",
                            verdict=ev["verdict"], score=ev["score"], by="test · VP")
        r = self.ask("Is the new seller's territory pure? What should I do to fix it?")
        self.assertIn("not pure", r["text"])
        self.assertIn("Ortris Media", r["text"])
        self.assertEqual(r["proposals"][0]["to_territory_id"], "APAC-A03-G1")
        r = self.ask("Should I implement the fix?")
        self.assertIn("My recommendation: yes", r["text"])
        self.assertIn("It's your call", r["text"])
        self.assertEqual(len(self.store.moves()), 1)                 # the Advisor never applies the fix itself
        for t in ("decline_drivers", "pipeline_summary"):
            self.assertIn(t, self.tk().SPECS)

    def test_every_answer_in_flow_is_grounded(self):
        for q in ["Which areas are declining?", "Who owns the top declining?", "Show me his entire book",
                  "What's the opportunity pipeline for it?", "Is Dana Pemberton's territory pure?", "Should I implement the fix?"]:
            r = self.ask(q)
            self.assertTrue(r["grounding"]["all_traceable"], (q, r["grounding"]))

    def test_should_without_proposals_and_book_without_subject(self):
        self.assertIn("haven't proposed anything", self.ask("Should I implement the fix?")["text"])
        r = self.ask("show me his entire book")
        self.assertNotIn("seller_book", [c["tool"] for c in r["tool_calls"]])


@unittest.skipUnless(HAS_PD, "pandas required")
class BriefingGrounding(AdvisorBase):
    def test_briefing_is_fully_traceable_for_every_persona(self):
        from planning.scope import personas
        for p in personas(self.d)[:6]:
            self.persona = p
            r = self.adv.respond("brief me", {})
            self.assertTrue(r["grounding"]["all_traceable"], (p.id, r["grounding"]))
