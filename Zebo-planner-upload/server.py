"""Norbae AI Annual Sales Planning Advisor — local dashboard server.

Python standard library only. Serves the static dashboard in ``web/`` and a small
JSON API on top of the deterministic tools. Binds to 127.0.0.1 by default.

    python server.py            # then open http://127.0.0.1:8501
    python server.py --port 8600

Nothing in this server writes to ``data/``. The only files it writes are local
demo records under ``local_state/`` (draft requests, review notes, audit log,
evaluation runs).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import webbrowser
from collections import OrderedDict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader: only NORBAE_* keys, never overrides real environment variables."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k.startswith("NORBAE_") and k not in os.environ:
            os.environ[k] = v.strip().strip('"').strip("'")


_load_dotenv(ROOT / ".env")

from agent_blueprint import (PROHIBITED_ACTIONS, create_change_request, evidence_for_territory,  # noqa: E402
                             execute_read_only_tool, tool_catalog)
from analysis_tools import (DataValidationError, check_data_consistency, evaluate_all_territories,  # noqa: E402
                            source_fingerprint, summarize_plan, validate_all_data)
from case_study import build_case_study  # noqa: E402
from config import (MAX_TOOL_CALLS_PER_QUESTION, THRESHOLD_LABEL, Thresholds, data_dir,  # noqa: E402
                    state_dir)
from evaluation import run_evaluations  # noqa: E402
from intent_router import EXAMPLE_QUESTIONS, FALLBACK_TEXT, MODE_LABEL, answer_question  # noqa: E402
from llm_adapter import OllamaClient, get_llm_config, run_llm_agent  # noqa: E402
from review_store import ReviewStore, now_iso  # noqa: E402

WEB_DIR = ROOT / "web"
STATIC_FILES = {"/": "index.html", "/index.html": "index.html", "/app.css": "app.css", "/app.js": "app.js"}
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".js": "application/javascript; charset=utf-8"}
MAX_BODY = 64 * 1024
TERRITORY_PATH = re.compile(r"^/api/territory/([A-Za-z0-9_-]{1,12})$")
REVIEW_PATH = re.compile(r"^/api/requests/(CR-[A-F0-9]{8})/review$")


class AppState:
    """Per-process state. Data/state dirs are resolved at startup (overridable for tests)."""

    def __init__(self, data: Path | None = None, state: Path | None = None):
        self.data_dir = Path(data) if data else data_dir()
        self.store = ReviewStore(state if state else state_dir())
        self.startup_fingerprint = source_fingerprint(self.data_dir)
        self.started_at = now_iso()
        self.llm = get_llm_config()
        self._drafts: OrderedDict[str, dict[str, Any]] = OrderedDict()  # unsaved advisor drafts (bounded)
        self._lock = threading.Lock()

    def remember_draft(self, draft: dict[str, Any]) -> None:
        with self._lock:
            self._drafts[draft["request_id"]] = draft
            while len(self._drafts) > 50:
                self._drafts.popitem(last=False)

    def pop_draft(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._drafts.pop(request_id, None)


def thresholds_from(params: dict[str, Any] | None) -> Thresholds:
    return Thresholds.from_mapping(params or {})


class Handler(BaseHTTPRequestHandler):
    server_version = "NorbaeDemo/1.0"
    app: AppState  # set on the class by make_server

    # ------------------------------------------------------------------ plumbing
    def log_message(self, fmt: str, *args: Any) -> None:  # keep the console quiet; no request bodies logged
        if os.environ.get("NORBAE_HTTP_LOG"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _headers(self, status: int, ctype: str, length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; "
                         "connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"error": message}, status)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("Request body too large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("Request body must be valid JSON")
        if not isinstance(data, dict):
            raise ValueError("Request body must be a JSON object")
        return data

    def _query(self) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    # ------------------------------------------------------------------ routing
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path in STATIC_FILES:
                return self._static(STATIC_FILES[path])
            if path == "/api/meta":
                return self._json(self._meta())
            if path == "/api/overview":
                return self._json(self._overview(thresholds_from(self._query())))
            if path == "/api/territories":
                t = thresholds_from(self._query())
                return self._json({**evaluate_all_territories(self.app.data_dir, t),
                                   "consistency": check_data_consistency(self.app.data_dir, t)})
            m = TERRITORY_PATH.match(path)
            if m:
                t = thresholds_from(self._query())
                r = execute_read_only_tool("get_territory_detail", _data_dir=self.app.data_dir, _thresholds=t,
                                           territory_id=m.group(1))
                if "error" in r:
                    return self._json(r, 404 if r["error"].startswith("Unknown") else 400)
                r["draft_evidence"] = evidence_for_territory(m.group(1), self.app.data_dir, t)
                return self._json(r)
            if path == "/api/requests":
                return self._json({"requests": list(reversed(self.app.store.list_requests())),
                                   "integrity": self._integrity()})
            if path == "/api/evaluations":
                return self._json({"last_run": self.app.store.last_eval_run(),
                                   "manual": self.app.store.list_manual_evaluations(),
                                   "last_test_run": self.app.store._read_json("last_test_run.json", None)})
            if path == "/api/audit":
                return self._json({"entries": self.app.store.read_audit(100)})
            if path == "/api/integrity":
                return self._json(self._integrity())
            if path == "/api/case-study":
                return self._json({"markdown": build_case_study(self.app.store, self.app.data_dir)})
            if path == "/api/case-study.md":
                body = build_case_study(self.app.store, self.app.data_dir).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="norbae_case_study.md"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return None
            return self._error("Not found", 404)
        except DataValidationError as exc:
            return self._error(f"Data validation error: {exc}", 422)
        except ValueError as exc:
            return self._error(str(exc), 400)
        except Exception:  # never leak stack traces or paths
            return self._error("Internal error while handling the request", 500)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            body = self._body()
            if path == "/api/ask":
                return self._json(self._ask(body))
            if path == "/api/scenario":
                r = execute_read_only_tool("preview_quota_scenario", _data_dir=self.app.data_dir,
                                           _thresholds=thresholds_from(body.get("thresholds")),
                                           territory_id=body.get("territory_id"),
                                           alternative_quota_usd=body.get("alternative_quota_usd"))
                return self._json(r, 400 if "error" in r else 200)
            if path == "/api/requests":
                return self._create_request(body)
            if path == "/api/requests/save-draft":
                draft = self.app.pop_draft(str(body.get("request_id", "")))
                if draft is None:
                    return self._error("Draft not found or already saved", 404)
                return self._json({"request": self.app.store.save_request(draft), "integrity": self._integrity()})
            m = REVIEW_PATH.match(path)
            if m:
                try:
                    rec = self.app.store.review_request(m.group(1), str(body.get("action", "")),
                                                        str(body.get("note", "")), str(body.get("reviewer", "")))
                except KeyError:
                    return self._error("Unknown request_id", 404)
                return self._json({"request": rec, "integrity": self._integrity()})
            if path == "/api/evaluations/run":
                return self._json(run_evaluations(self.app.data_dir, self.app.store))
            if path == "/api/evaluations/manual":
                rec = self.app.store.record_manual_evaluation(str(body.get("test_id", "")), str(body.get("outcome", "")),
                                                              str(body.get("reviewer", "")), str(body.get("notes", "")))
                return self._json({"record": rec})
            if path == "/api/tests/run":
                return self._json(self._run_unit_tests())
            return self._error("Not found", 404)
        except DataValidationError as exc:
            return self._error(f"Data validation error: {exc}", 422)
        except ValueError as exc:
            return self._error(str(exc), 400)
        except Exception:
            return self._error("Internal error while handling the request", 500)

    def do_PUT(self) -> None:  # noqa: N802
        self._error("Method not allowed", 405)

    do_DELETE = do_PATCH = do_PUT

    # ------------------------------------------------------------------ handlers
    def _static(self, name: str) -> None:
        p = WEB_DIR / name
        if not p.is_file():
            return self._error("Not found", 404)
        data = p.read_bytes()
        self._headers(200, CONTENT_TYPES[p.suffix], len(data))
        self.wfile.write(data)

    def _meta(self) -> dict[str, Any]:
        return {
            "app": "Norbae AI Annual Sales Planning Advisor",
            "positioning": "Norbae AI helps businesses improve complex workflows with practical AI, measurable evaluation, and human oversight.",
            "synthetic_data": True,
            "mode_label": (f"Optional local model enabled ({self.app.llm.model}) — experimental; falls back to the no-LLM baseline."
                           if self.app.llm.enabled else MODE_LABEL),
            "llm": self.app.llm.public(),
            "default_thresholds": Thresholds().to_dict(),
            "threshold_label": THRESHOLD_LABEL,
            "example_questions": EXAMPLE_QUESTIONS,
            "fallback_text": FALLBACK_TEXT,
            "tools": tool_catalog(),
            "max_tool_calls": MAX_TOOL_CALLS_PER_QUESTION,
            "prohibited_actions": PROHIBITED_ACTIONS,
            "data_validation": validate_all_data(self.app.data_dir),
            "started_at": self.app.started_at,
        }

    def _overview(self, t: Thresholds) -> dict[str, Any]:
        summary = summarize_plan(self.app.data_dir)
        territories = evaluate_all_territories(self.app.data_dir, t)["territories"]
        flagged = [x for x in territories if x["flags"]]
        return {
            "summary": summary,
            "exception_count": len(flagged),
            "exception_ids": [x["territory_id"] for x in flagged],
            "territories": [{k: x[k] for k in ("territory_id", "territory_name", "region", "company_target_usd",
                                                "proposed_quota_usd", "variance_usd", "variance_pct", "flags")}
                            for x in territories],
            "thresholds": t.to_dict(),
            "threshold_label": THRESHOLD_LABEL,
            "consistency_findings": check_data_consistency(self.app.data_dir, t)["finding_count"],
            "integrity": self._integrity(),
        }

    def _ask(self, body: dict[str, Any]) -> dict[str, Any]:
        question = str(body.get("question", ""))[:2000]
        t = thresholds_from(body.get("thresholds"))
        if self.app.llm.enabled and body.get("use_llm", True):
            client = OllamaClient(self.app.llm.model, self.app.llm.url)
            r = run_llm_agent(question, client, data_dir=self.app.data_dir, thresholds=t, store=self.app.store)
        else:
            r = answer_question(question, data_dir=self.app.data_dir, thresholds=t, store=self.app.store)
        if r.get("draft") and r["draft"].get("status") == "PENDING_HUMAN_APPROVAL":
            self.app.remember_draft(r["draft"])
        r["asked_at"] = now_iso()
        return r

    def _create_request(self, body: dict[str, Any]) -> None:
        tid = body.get("territory_id")
        rationale = str(body.get("rationale", "")).strip()
        if not tid:
            return self._error("territory_id is required")
        if not rationale:
            return self._error("A business rationale is required")
        t = thresholds_from(body.get("thresholds"))
        evidence = evidence_for_territory(str(tid), self.app.data_dir, t)
        extra = [e for e in str(body.get("additional_evidence", "")).splitlines() if e.strip()]
        evidence += [f"Planner note: {e.strip()}" for e in extra]
        draft = create_change_request("propose_quota_review", territory_id=tid,
                                      suggested_quota_usd=body.get("suggested_quota_usd") or None,
                                      rationale=rationale, assumptions=str(body.get("assumptions", "")),
                                      evidence=evidence, reviewer=str(body.get("reviewer", "")),
                                      data_dir=self.app.data_dir)
        if draft["status"] != "PENDING_HUMAN_APPROVAL":
            return self._json({"error": "; ".join(draft.get("errors", ["Invalid draft"]))}, 400)
        return self._json({"request": self.app.store.save_request(draft), "integrity": self._integrity()})

    def _integrity(self) -> dict[str, Any]:
        now = source_fingerprint(self.app.data_dir)
        return {"unchanged_since_start": now == self.app.startup_fingerprint, "checked_at": now_iso(),
                "since": self.app.started_at,
                "files": {k: {"sha256_prefix": v[:12], "unchanged": v == self.app.startup_fingerprint.get(k)}
                          for k, v in now.items()}}

    def _run_unit_tests(self) -> dict[str, Any]:
        """Runs the project's own unittest suite (fixed command; never built from user input)."""
        env = {**os.environ, "NORBAE_SKIP_SERVER_SELFTEST": "1"}
        try:
            proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                                  cwd=ROOT, capture_output=True, text=True, timeout=180, env=env)
        except subprocess.TimeoutExpired:
            return {"error": "Unit test run timed out"}
        out = proc.stderr + proc.stdout
        lines = out.splitlines()
        total = next((int(m.group(1)) for line in lines if (m := re.match(r"Ran (\d+) tests?", line))), 0)
        fail = next((line for line in reversed(lines) if line.startswith(("OK", "FAILED"))), "")
        def count(key: str) -> int:
            m = re.search(rf"{key}=(\d+)", fail)
            return int(m.group(1)) if m else 0
        failed, errors, skipped = count("failures"), count("errors"), count("skipped")
        result = {"run_at": now_iso(), "total": total, "failed": failed, "errors": errors, "skipped": skipped,
                  "passed": total - failed - errors - skipped, "ok": proc.returncode == 0,
                  "status_line": fail, "output_tail": "\n".join(lines[-60:])}
        self.app.store._write_json("last_test_run.json", {k: v for k, v in result.items() if k != "output_tail"})
        self.app.store.append_audit("unit_tests_run", total=total, ok=result["ok"])
        return result


def make_server(host: str = "127.0.0.1", port: int = 8501, data: Path | None = None,
                state: Path | None = None) -> ThreadingHTTPServer:
    app = AppState(data, state)
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the Norbae AI Annual Sales Planning Advisor locally.")
    ap.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1, local only)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("NORBAE_PORT", 8501)))
    ap.add_argument("--open", action="store_true", help="Open the dashboard in your default browser")
    args = ap.parse_args()
    server = make_server(args.host, args.port)
    url = f"http://{args.host}:{server.server_address[1]}"
    print(f"Norbae AI Annual Sales Planning Advisor running at {url}  (synthetic demo data; Ctrl+C to stop)")
    print(f"Mode: {server.RequestHandlerClass.app.llm.public()}")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
