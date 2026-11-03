"""Local dashboard server: API behaviour, no hardcoded KPIs, read-only guarantees, safe errors."""
import json
import os
import re
import sys
import threading
import unittest
import urllib.error
import urllib.request

from _helpers import ROOT, TempDirs

sys.path.insert(0, str(ROOT))
import server  # noqa: E402

from analysis_tools import source_fingerprint, summarize_plan  # noqa: E402


class ServerCase(unittest.TestCase):
    def setUp(self):
        self.dirs = TempDirs().__enter__()
        self.srv = server.make_server("127.0.0.1", 0, data=self.dirs.data, state=self.dirs.state)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.dirs.__exit__(None, None, None)

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=20) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def post(self, path, body):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def jget(self, path):
        code, text = self.get(path)
        return code, json.loads(text)


class ApiTests(ServerCase):
    def test_overview_values_come_from_csv(self):
        code, o = self.jget("/api/overview")
        self.assertEqual(code, 200)
        self.assertEqual(o["summary"]["proposed_quota_total_usd"], 91_175_000)
        self.assertEqual(o["exception_ids"], ["T05", "T10"])
        self.assertTrue(o["integrity"]["unchanged_since_start"])

    def test_overview_changes_when_csv_changes(self):
        """Proves the KPI is computed at runtime: edit the CSV, the API value follows."""
        self.dirs.write_quota_plan([["Z1", 500, 700, 1, 500, 1.0], ["Z2", 100, 100, 1, 100, 1.0]])
        code, o = self.jget("/api/overview")
        self.assertEqual(o["summary"]["company_target_usd"], 600)
        self.assertEqual(o["summary"]["proposed_quota_total_usd"], 800)
        self.assertEqual(o["exception_ids"], ["Z1"])

    def test_threshold_query_and_validation(self):
        code, o = self.jget("/api/overview?variance_pct=6")
        self.assertEqual(o["exception_ids"], ["T05"])
        code, o = self.jget("/api/overview?variance_pct=abc")
        self.assertEqual(code, 400)

    def test_territory_endpoint(self):
        code, d = self.jget("/api/territory/T05")
        self.assertEqual(code, 200)
        self.assertTrue(d["draft_evidence"])
        code, d = self.jget("/api/territory/T99")
        self.assertEqual(code, 404)
        self.assertNotIn("proposed_quota_usd", d)

    def test_ask_and_save_draft_flow_never_changes_data(self):
        before = source_fingerprint(self.dirs.data)
        code, r = self.post("/api/ask", {"question": "Draft a proposed review request for territory T05, but do not apply it."})
        self.assertEqual(code, 200)
        rid = r["draft"]["request_id"]
        code, s = self.post("/api/requests/save-draft", {"request_id": rid})
        self.assertEqual(code, 200)
        self.assertEqual(s["request"]["status"], "PENDING_HUMAN_APPROVAL")
        code, again = self.post("/api/requests/save-draft", {"request_id": rid})
        self.assertEqual(code, 404)  # one-time save; client cannot replay or forge
        code, rv = self.post(f"/api/requests/{rid}/review", {"action": "mark_reviewed", "note": "ok", "reviewer": "QA"})
        self.assertEqual(rv["request"]["status"], "REVIEWED_IN_DEMO")
        self.assertFalse(rv["request"]["executed"])
        self.assertTrue(rv["integrity"]["unchanged_since_start"])
        self.assertEqual(source_fingerprint(self.dirs.data), before)

    def test_create_request_validation(self):
        code, r = self.post("/api/requests", {"territory_id": "T05"})
        self.assertEqual(code, 400)
        code, r = self.post("/api/requests", {"territory_id": "T99", "rationale": "x"})
        self.assertEqual(code, 400)
        code, r = self.post("/api/requests", {"territory_id": "T10", "rationale": "Boundary case",
                                              "suggested_quota_usd": "5000000", "executed": True})
        self.assertEqual(code, 200)
        self.assertFalse(r["request"]["executed"])  # client-supplied 'executed' ignored
        self.assertEqual(r["request"]["current_proposed_quota_usd"], 5_250_000)

    def test_review_requires_note(self):
        code, r = self.post("/api/requests", {"territory_id": "T05", "rationale": "x"})
        rid = r["request"]["request_id"]
        code, e = self.post(f"/api/requests/{rid}/review", {"action": "mark_reviewed", "note": ""})
        self.assertEqual(code, 400)

    def test_evaluation_endpoints(self):
        code, run = self.post("/api/evaluations/run", {})
        self.assertEqual(run["summary"]["automated_failed"], 0)
        self.assertEqual(run["summary"]["manual_pending"], 4)
        code, rec = self.post("/api/evaluations/manual", {"test_id": "TC08", "outcome": "Fail", "reviewer": "QA"})
        self.assertEqual(code, 200)

    def test_case_study_reports_only_recorded_runs(self):
        code, cs = self.jget("/api/case-study")
        self.assertIn("Not yet run in this environment", cs["markdown"])
        self.post("/api/evaluations/run", {})
        code, cs = self.jget("/api/case-study")
        self.assertIn("automated cases passed", cs["markdown"])
        self.assertIn("$91,175,000", cs["markdown"])

    def test_bad_requests_are_safe(self):
        code, r = self.post("/api/ask", b"not json")
        self.assertEqual(code, 400)
        code, r = self.post("/api/ask", b"x" * (70 * 1024))
        self.assertEqual(code, 400)
        code, text = self.get("/../data/quota_plan.csv")
        self.assertEqual(code, 404)
        code, text = self.get("/server.py")
        self.assertEqual(code, 404)
        code, text = self.get("/api/nope")
        self.assertEqual(code, 404)
        self.assertNotIn(str(ROOT), text)

    def test_missing_data_returns_useful_error(self):
        (self.dirs.data / "quota_plan.csv").unlink()
        code, r = self.jget("/api/overview")
        self.assertEqual(code, 422)
        self.assertIn("data/quota_plan.csv", r["error"])
        self.assertNotIn(str(self.dirs.tmp), r["error"])

    @unittest.skipIf(os.environ.get("NORBAE_SKIP_SERVER_SELFTEST"), "avoid recursion when run from the UI")
    def test_static_files_and_security_headers(self):
        with urllib.request.urlopen(self.base + "/", timeout=10) as r:
            self.assertIn("Synthetic demo data", r.read().decode())
            self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"])


class NoHardcodedKpis(unittest.TestCase):
    def test_ui_and_server_contain_no_kpi_literals(self):
        s = summarize_plan()
        values = [s["company_target_usd"], s["proposed_quota_total_usd"], s["quota_minus_target_usd"]]
        patterns = set()
        for v in values:
            n = int(v)
            patterns |= {str(n), f"{n:,}", f"{n / 1e6:g}M", f"{n / 1e6:g} million"}
        patterns |= {"1.31%", "T05, T10"}
        for rel in ["web/index.html", "web/app.js", "server.py"]:
            text = (ROOT / rel).read_text(encoding="utf-8")
            for p in patterns:
                with self.subTest(file=rel, literal=p):
                    self.assertNotIn(p, text)
        js = (ROOT / "web/app.js").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\b\d{7,}\b", js), "large numeric literal found in app.js")


if __name__ == "__main__":
    unittest.main()
