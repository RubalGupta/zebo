"""Local demo review records and audit log.

DEMO-ONLY WORKFLOW SIMULATION.
- Stores draft change requests, demo review decisions, manual evaluation records,
  and an audit log as JSON files under ``local_state/`` (gitignored).
- Never writes to ``data/`` or any business system. Nothing here can change a
  quota, territory, CRM record, or the planning source data.
- No secrets, API keys, or free-text questions are written to the audit log.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import state_dir as _default_state_dir

_LOCK = threading.RLock()

PENDING = "PENDING_HUMAN_APPROVAL"
REVIEWED = "REVIEWED_IN_DEMO"
RETURNED = "RETURNED_FOR_REVISION"
REVIEW_ACTIONS = {"mark_reviewed": REVIEWED, "return_for_revision": RETURNED}

MAX_TEXT = 1000
MAX_NOTE = 500
MAX_NAME = 80
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean_text(value: Any, limit: int = MAX_TEXT) -> str:
    if value is None:
        return ""
    text = _CONTROL_CHARS.sub("", str(value)).strip()
    return text[:limit]


class ReviewStore:
    def __init__(self, base_dir: Path | str | None = None):
        self.base = Path(base_dir) if base_dir is not None else _default_state_dir()

    # ------------------------------------------------------------------ io helpers
    def _path(self, name: str) -> Path:
        return self.base / name

    def _read_json(self, name: str, default: Any) -> Any:
        p = self._path(name)
        if not p.is_file():
            return default
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default

    def _write_json(self, name: str, payload: Any) -> None:
        self.base.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.base, prefix=".tmp_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, self._path(name))

    # ------------------------------------------------------------------ audit log
    def append_audit(self, event: str, **fields: Any) -> dict[str, Any]:
        """Append a structured audit entry. Callers pass only safe, non-sensitive fields."""
        entry = {"timestamp": now_iso(), "event": event}
        for k, v in fields.items():
            if k.lower() in {"api_key", "secret", "token", "password", "question", "prompt"}:
                continue  # defensive: never log secrets or raw user text
            entry[k] = v
        with _LOCK:
            self.base.mkdir(parents=True, exist_ok=True)
            with open(self._path("audit_log.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        return entry

    def read_audit(self, limit: int = 200) -> list[dict[str, Any]]:
        p = self._path("audit_log.jsonl")
        if not p.is_file():
            return []
        lines = p.read_text(encoding="utf-8").splitlines()[-limit:]
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return list(reversed(out))

    # ------------------------------------------------------------------ change requests
    def list_requests(self) -> list[dict[str, Any]]:
        with _LOCK:
            return self._read_json("change_requests.json", [])

    def get_request(self, request_id: str) -> dict[str, Any] | None:
        return next((r for r in self.list_requests() if r.get("request_id") == request_id), None)

    def save_request(self, draft: dict[str, Any]) -> dict[str, Any]:
        """Persist a draft produced by ``agent_blueprint.create_change_request`` to the local queue."""
        if draft.get("status") != PENDING or draft.get("executed") is not False:
            raise ValueError("Only unexecuted PENDING_HUMAN_APPROVAL drafts can be saved")
        record = dict(draft)
        record.setdefault("review_history", [])
        record["saved_at"] = now_iso()
        with _LOCK:
            items = self._read_json("change_requests.json", [])
            if any(r.get("request_id") == record["request_id"] for r in items):
                raise ValueError("A request with this ID already exists")
            items.append(record)
            self._write_json("change_requests.json", items)
        self.append_audit("change_request_saved", request_id=record["request_id"],
                          territory_id=record.get("territory_id"), status=PENDING, executed=False,
                          simulation=True)
        return record

    def review_request(self, request_id: str, action: str, note: str, reviewer: str = "") -> dict[str, Any]:
        """Demo-only review decision. Updates the local record; NEVER applies the change."""
        if action not in REVIEW_ACTIONS:
            raise ValueError(f"Unsupported review action '{action}'. Allowed: {', '.join(REVIEW_ACTIONS)}")
        note = clean_text(note, MAX_NOTE)
        if not note:
            raise ValueError("An audit note is required for every review decision")
        reviewer = clean_text(reviewer, MAX_NAME)
        with _LOCK:
            items = self._read_json("change_requests.json", [])
            rec = next((r for r in items if r.get("request_id") == request_id), None)
            if rec is None:
                raise KeyError(f"Unknown request_id: {request_id}")
            if rec["status"] != PENDING:
                raise ValueError(f"Request is already {rec['status']}; only pending drafts can be reviewed")
            new_status = REVIEW_ACTIONS[action]
            rec["status"] = new_status
            rec["executed"] = False                 # invariant: never executed
            rec["applied_to_source_data"] = False   # invariant: never applied
            rec.setdefault("review_history", []).append({
                "timestamp": now_iso(), "action": action, "new_status": new_status,
                "reviewer": reviewer or "(not provided)", "note": note,
                "simulation": True,
            })
            self._write_json("change_requests.json", items)
        self.append_audit("change_request_reviewed", request_id=request_id, action=action,
                          new_status=new_status, executed=False, simulation=True)
        return rec

    # ------------------------------------------------------------------ manual evaluations
    def list_manual_evaluations(self) -> dict[str, dict[str, Any]]:
        with _LOCK:
            return self._read_json("manual_evaluations.json", {})

    def record_manual_evaluation(self, test_id: str, outcome: str, reviewer: str, notes: str) -> dict[str, Any]:
        outcome = clean_text(outcome, 10).capitalize()
        if outcome not in {"Pass", "Fail"}:
            raise ValueError("outcome must be 'Pass' or 'Fail'")
        reviewer = clean_text(reviewer, MAX_NAME)
        if not reviewer:
            raise ValueError("A reviewer name or role is required to record a manual evaluation")
        rec = {"test_id": clean_text(test_id, 20), "outcome": outcome, "reviewer": reviewer,
               "notes": clean_text(notes, MAX_NOTE), "recorded_at": now_iso()}
        with _LOCK:
            items = self._read_json("manual_evaluations.json", {})
            items[rec["test_id"]] = rec
            self._write_json("manual_evaluations.json", items)
        self.append_audit("manual_evaluation_recorded", test_id=rec["test_id"], outcome=outcome)
        return rec

    # ------------------------------------------------------------------ last automated eval run
    def save_eval_run(self, run: dict[str, Any]) -> None:
        with _LOCK:
            self._write_json("last_eval_run.json", run)

    def last_eval_run(self) -> dict[str, Any] | None:
        return self._read_json("last_eval_run.json", None)
