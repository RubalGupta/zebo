"""Smoke tests for streamlit_classic.py.

* If Streamlit IS installed: runs the real app headlessly with streamlit.testing.v1.AppTest.
* Always: executes every code path of the script against a lightweight recording stub of the
  `streamlit` module (so the script's own logic is exercised even where Streamlit can't be installed).
The stub checks our code, not Streamlit's rendering.
"""
import importlib.util
import os
import runpy
import sys
import types
import unittest

from _helpers import ROOT, TempDirs

from analysis_tools import source_fingerprint

APP = str(ROOT / "streamlit_classic.py")
HAS_STREAMLIT = importlib.util.find_spec("streamlit") is not None


class _Rerun(Exception):
    pass


class _Stop(Exception):
    pass


class _State(dict):
    __getattr__ = dict.__getitem__

    def __setattr__(self, k, v):
        self[k] = v


class _Recorder:
    """Accepts any st.* call; returns sensible values for widgets."""

    def __init__(self, inputs, press, log):
        self._inputs, self._press, self._log = inputs, press, log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __getattr__(self, name):
        def call(*args, **kwargs):
            label = str(args[0]) if args else str(kwargs.get("label", ""))
            self._log.append((name, label))
            if name == "tabs":
                return [self._child() for _ in args[0]]
            if name == "columns":
                n = args[0] if isinstance(args[0], int) else len(args[0])
                return [self._child() for _ in range(n)]
            if name in ("button", "form_submit_button"):
                return any(p in label for p in self._press)
            if name == "selectbox":
                opts = list(args[1]) if len(args) > 1 else list(kwargs.get("options", []))
                want = self._inputs.get(label)
                return want if want in opts else (opts[kwargs.get("index", 0)] if opts else None)
            if name == "radio":
                return list(args[1])[0]
            if name == "number_input":
                if label in self._inputs:
                    return self._inputs[label]
                return kwargs.get("value", args[3] if len(args) > 3 else 0)
            if name in ("text_input", "text_area"):
                return self._inputs.get(label, "")
            if name == "rerun":
                raise _Rerun()
            if name == "stop":
                raise _Stop()
            return self._child()
        return call

    def _child(self):
        return _Recorder(self._inputs, self._press, self._log)


def run_with_stub(inputs=None, press=(), state=None):
    log = []
    root = _Recorder(inputs or {}, press, log)
    mod = types.ModuleType("streamlit")
    mod.__getattr__ = root.__getattr__
    mod.session_state = state if state is not None else _State()
    cc = types.SimpleNamespace(NumberColumn=lambda *a, **k: None)
    mod.column_config = cc
    testing = types.ModuleType("streamlit.testing")
    old = sys.modules.get("streamlit")
    sys.modules["streamlit"] = mod
    try:
        try:
            runpy.run_path(APP, run_name="__main__")
        except _Rerun:
            log.append(("rerun", ""))
    finally:
        if old is None:
            sys.modules.pop("streamlit", None)
        else:
            sys.modules["streamlit"] = old
    return log, mod.session_state


class StreamlitScriptLogic(unittest.TestCase):
    def setUp(self):
        self.dirs = TempDirs().__enter__()
        self._old = os.environ.get("NORBAE_DATA_DIR")
        os.environ["NORBAE_DATA_DIR"] = str(self.dirs.data)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("NORBAE_DATA_DIR", None)
        else:
            os.environ["NORBAE_DATA_DIR"] = self._old
        self.dirs.__exit__(None, None, None)

    def test_initial_render_shows_csv_totals(self):
        log, state = run_with_stub()
        metrics = {label for name, label in log if name == "metric"}
        self.assertTrue({"Company target", "Proposed quota total", "Planning exceptions"} <= metrics)
        self.assertEqual(state["thresholds"]["variance_pct"], 5.0)
        warnings = [label for name, label in log if name == "warning"]
        self.assertTrue(any("$1,175,000" in w for w in warnings))

    def test_ask_creates_grounded_answer_and_draft_is_not_saved_automatically(self):
        q = "Draft a proposed review request for territory T05, but do not apply it."
        before = source_fingerprint(self.dirs.data)
        log, state = run_with_stub(inputs={"Your question": q}, press=("Ask",))
        r = state["conversation"][0]
        self.assertEqual(r["intent"], "draft_change_request")
        self.assertTrue(r["grounding"]["all_traceable"])
        self.assertIn(r["draft"]["request_id"], state["unsaved_drafts"])
        self.assertFalse((self.dirs.state / "change_requests.json").exists())
        self.assertEqual(source_fingerprint(self.dirs.data), before)

    def test_create_draft_requires_rationale_and_never_changes_data(self):
        before = source_fingerprint(self.dirs.data)
        log, _ = run_with_stub(press=("Create draft",))
        self.assertIn(("error", "A business rationale is required."), log)
        log, _ = run_with_stub(inputs={"Reason / business rationale (required)": "Boundary case"},
                               press=("Create draft",))
        saved = (self.dirs.state / "change_requests.json").read_text()
        self.assertIn("PENDING_HUMAN_APPROVAL", saved)
        self.assertIn('"executed": false', saved)
        self.assertEqual(source_fingerprint(self.dirs.data), before)
        # review without an audit note is refused; with a note it updates only the local record
        log, _ = run_with_stub(press=("Mark reviewed",))
        self.assertIn(("error", "An audit note is required for every review decision"), log)
        import json
        rid = json.loads((self.dirs.state / "change_requests.json").read_text())[0]["request_id"]
        log, _ = run_with_stub(inputs={"Audit note (required)": "checked"}, press=("Mark reviewed",))
        rec = json.loads((self.dirs.state / "change_requests.json").read_text())[0]
        self.assertEqual((rec["request_id"], rec["status"], rec["executed"]), (rid, "REVIEWED_IN_DEMO", False))
        self.assertEqual(source_fingerprint(self.dirs.data), before)

    def test_threshold_form_and_evaluation_lab(self):
        log, state = run_with_stub(inputs={"Variance ≥ (%)": 6.0}, press=("Apply",))
        self.assertEqual(state["thresholds"]["variance_pct"], 6.0)
        self.assertEqual(log[-1][0], "rerun")
        log, state = run_with_stub(press=("Run automated checks",))
        metrics = [label for name, label in log if name == "metric"]
        self.assertIn("Manual pending", metrics)

    def test_no_hardcoded_kpi_literals(self):
        text = (ROOT / "streamlit_classic.py").read_text(encoding="utf-8")
        for literal in ["90,000,000", "91,175,000", "1,175,000", "90000000", "91175000"]:
            self.assertNotIn(literal, text)


@unittest.skipUnless(HAS_STREAMLIT, "Streamlit not installed (pip install -r requirements-streamlit.txt)")
class StreamlitAppTest(unittest.TestCase):
    def test_app_runs_headless(self):
        from streamlit.testing.v1 import AppTest
        with TempDirs():
            at = AppTest.from_file(APP, default_timeout=60).run()
            self.assertFalse(at.exception, at.exception)
            labels = [m.label for m in at.metric]
            self.assertIn("Company target", labels)
            target = next(m for m in at.metric if m.label == "Company target")
            self.assertEqual(target.value, "$90,000,000")


if __name__ == "__main__":
    unittest.main()
