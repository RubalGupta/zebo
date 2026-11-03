"""FY27 cockpit (streamlit_app.py) script tests via the recording stub; plus a real AppTest when Streamlit exists."""
import importlib.util
import os
import unittest

import _st_stub
from _helpers import ROOT, TempDirs

APP = ROOT / "streamlit_app.py"
HAS_ST = importlib.util.find_spec("streamlit") is not None and importlib.util.find_spec("pandas") is not None


def fy27_fingerprint():
    import hashlib
    d = ROOT / "data" / "fy27"
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.glob("*.csv"))}


@unittest.skipUnless(importlib.util.find_spec("pandas"), "pandas required")
class CockpitScript(unittest.TestCase):
    def setUp(self):
        self.dirs = TempDirs().__enter__()

    def tearDown(self):
        self.dirs.__exit__(None, None, None)

    def test_opens_on_advisor_briefing_scoped_to_division(self):
        log, state, _ = _st_stub.run(APP)
        self.assertEqual(state["persona_id"], "P-NAW")
        first = state["messages"][0]
        self.assertEqual(first["role"], "assistant")
        self.assertIn("North America West", first["text"])
        self.assertTrue(first["grounding"]["all_traceable"])
        self.assertIn(("popover", "🤖 Advisor"), log)          # floating agent launcher
        self.assertIn(("text_input", "Ask the Advisor"), log)

    def test_chat_drilldown_sets_workspace_focus(self):
        state = _st_stub.State()
        _st_stub.run(APP, state=state)
        _st_stub.run(APP, inputs={"advisor_q": "drill into Pacific Northwest"}, press=("Send",), state=state)
        self.assertEqual(state["ctx"]["focus"], ("area", "NAW-A02"))
        _st_stub.run(APP, inputs={"advisor_q": "which accounts are declining?"}, press=("Send",), state=state)
        self.assertIn("Accounts in Pacific Northwest", state["messages"][-1]["text"])

    def test_scope_is_enforced_in_chat(self):
        state = _st_stub.State()
        _st_stub.run(APP, inputs={"advisor_q": "show me EMEA"}, press=("Send",), state=state)
        self.assertIn("outside your access", state["messages"][-1]["text"])

    def test_apply_move_requires_reason_and_writes_only_draft_plan(self):
        before = fy27_fingerprint()
        state = _st_stub.State()
        _st_stub.run(APP, inputs={"advisor_q": "move Selridge Energy to Southern California Deep 1"}, press=("Send",), state=state)
        msgs = state["messages"]
        self.assertEqual(len(msgs[-1]["proposals"]), 1)
        i = len(msgs) - 1
        # without a reason the apply button is disabled (stub: we press it anyway -> app must not apply w/o reason)
        _st_stub.run(APP, inputs={}, press=(f"apply_chat_{i}_0",), state=state)
        self.assertFalse((self.dirs.state / "fy27_plan.json").exists())
        _, _, rerun = _st_stub.run(APP, inputs={f"reason_chat_{i}_0": "Align to Deep motion"},
                                   press=(f"apply_chat_{i}_0",), state=state)
        self.assertTrue(rerun)
        plan = (self.dirs.state / "fy27_plan.json").read_text()
        self.assertIn("A0091", plan)
        self.assertIn("Align to Deep motion", plan)
        self.assertEqual(fy27_fingerprint(), before)

    def test_hosted_mode_gives_each_session_a_private_plan(self):
        os.environ["NORBAE_PER_SESSION_PLAN"] = "1"
        try:
            plans = []
            for _ in range(2):          # two independent visitors
                state = _st_stub.State()
                _st_stub.run(APP, inputs={"advisor_q": "move Selridge Energy to Southern California Deep 1"},
                             press=("Send",), state=state)
                i = len(state["messages"]) - 1
                _st_stub.run(APP, inputs={f"reason_chat_{i}_0": "Align to Deep motion"},
                             press=(f"apply_chat_{i}_0",), state=state)
                plans.append(state["plan_dir"])
            self.assertNotEqual(plans[0], plans[1])
            for d in plans:
                self.assertIn("A0091", (__import__("pathlib").Path(d) / "fy27_plan.json").read_text())
            self.assertFalse((self.dirs.state / "fy27_plan.json").exists())   # shared local plan untouched
        finally:
            os.environ.pop("NORBAE_PER_SESSION_PLAN", None)

    def test_every_phase1_step_renders(self):
        for step in ("business", "accounts", "moves", "purity", "hierarchy", "revenue", "quota", "seller"):
            with self.subTest(step=step):
                state = _st_stub.State(step=step)
                log, state, _ = _st_stub.run(APP, state=state)
                self.assertIn(("markdown", f"#### " + {
                    "business": "1 · My business", "accounts": "2 · Accounts & owners", "moves": "3 · Plan moves",
                    "purity": "4 · Territory purity", "hierarchy": "5 · Hierarchy & comp  · Phase 2",
                    "revenue": "6 · Revenue lock  · Phase 2", "quota": "7 · Quota model  · Phase 3",
                    "seller": "8 · Seller view & disputes  · Phase 3"}[step]), log)

    def test_currency_is_never_rendered_as_latex(self):
        state = _st_stub.State()
        _st_stub.run(APP, inputs={"advisor_q": "drill into Pacific Northwest"}, press=("Send",), state=state)
        log, _, _ = _st_stub.run(APP, state=state)
        import re as _re
        for name, text in log:
            if name in ("markdown", "caption", "expander", "info", "warning") and "$" in text:
                self.assertIsNone(_re.search(r"(?<!\\)\$", text), f"unescaped $ in {name}: {text[:80]}")

    def test_workspace_insight_buttons_ask_the_advisor(self):
        state = _st_stub.State()
        _st_stub.run(APP, state=state)
        _st_stub.run(APP, press=("Top pipeline accounts",), state=state)
        self.assertIn("by open pipeline", state["messages"][-1]["text"])
        self.assertTrue(state.get("advisor_pop"))

    def test_persona_switch_rescopes(self):
        state = _st_stub.State(persona_id="P-EMEA-A02")
        _st_stub.run(APP, state=state)
        self.assertIn("DACH", state["messages"][0]["text"])

    def test_no_hardcoded_kpis(self):
        text = APP.read_text(encoding="utf-8")
        for lit in ("584.0", "104.7", "$104", "649"):
            self.assertNotIn(lit, text)


@unittest.skipUnless(HAS_ST, "Streamlit not installed (pip install -r requirements-streamlit.txt)")
class CockpitAppTest(unittest.TestCase):
    def test_real_streamlit_run(self):
        from streamlit.testing.v1 import AppTest
        with TempDirs():
            at = AppTest.from_file(str(APP), default_timeout=90).run()
            self.assertFalse(at.exception, at.exception)
            at.chat_input[0].set_value("drill into Pacific Northwest").run()
            self.assertFalse(at.exception, at.exception)
            for step in ("accounts", "moves", "purity"):
                at.sidebar.radio[0].set_value(step).run()
                self.assertFalse(at.exception, f"{step}: {at.exception}")


if __name__ == "__main__":
    unittest.main()
