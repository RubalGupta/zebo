# Architecture and guardrails

## Flow
User question → **intent router** (no-LLM baseline) *or* optional local model → **allowlisted read-only tool**
(validated arguments, max 3 calls) → **deterministic calculation** from the CSVs → **evidence-grounded response**
(every $ / % figure checked against tool output) → **human review** → optional **draft-only change request**
(`PENDING_HUMAN_APPROVAL`, `executed: false`, never applied).

```
 Browser (web/index.html + app.js, vanilla JS)
        │  JSON over HTTP, 127.0.0.1 only
        ▼
 server.py  (Python stdlib http.server)
        │
        ├── intent_router.py ──┐        llm_adapter.py (optional, OFF by default; local Ollama only)
        │                      ▼                │
        │              agent_blueprint.ToolSession  ◄────────┘
        │                 · fixed allowlist (TOOLS)
        │                 · typed argument validation
        │                 · 3-call limit, unknown tools fail closed
        │                 · audit log (tool, safe args, status, time)
        │                      │
        │                      ▼
        ├── analysis_tools.py   (read-only, deterministic; DataValidationError on bad input)
        │         ▲
        │         └── data/*.csv  (synthetic source of truth — never written)
        │
        ├── review_store.py   → local_state/*.json(l)  (drafts, demo reviews, audit log, eval runs)
        ├── evaluation.py     → runs tests/evaluation_cases.csv against a temp copy of data
        └── case_study.py     → Markdown export with live figures + recorded runs only
```

## Components
| Module | Responsibility |
|---|---|
| `src/config.py` | Illustrative thresholds (configurable, validated), paths, 3-call limit |
| `src/analysis_tools.py` | CSV loading/validation; `summarize_plan`, `find_planning_exceptions`, `get_territory_detail`, `run_all`, plus `check_data_consistency`, `list_data_gaps`, `preview_quota_scenario`, `source_fingerprint` |
| `src/agent_blueprint.py` | Tool registry/allowlist, argument specs, `execute_read_only_tool`, `ToolSession`, `create_change_request` |
| `src/intent_router.py` | No-LLM baseline: keyword intents → tools → answer; `grounding_check` |
| `src/llm_adapter.py` | Optional local model loop with code-level guardrails and baseline fallback |
| `src/review_store.py` | Local demo queue, review decisions, manual eval records, audit log |
| `src/evaluation.py` | Evaluation Lab runner (automated vs. manual cases) |
| `src/case_study.py` | Portfolio case study generator |
| `server.py` | Local JSON API + static file server; binds to 127.0.0.1 |
| `web/` | Dashboard (7 tabs), no external scripts, fonts, or CDNs |

## Agent boundaries (enforced in code)
- Read-only access to the demo data. No module writes to `data/`.
- No external writes, emails, CRM updates, quota changes, or territory changes. The only network listener is the
  local dashboard; the only outbound call possible is to a **localhost** Ollama server when explicitly enabled.
- Tool allowlist enforced in Python (`TOOLS`), not only in a prompt. Unknown tool names return
  `{"error": "Tool not allowlisted"}`.
- Arguments are validated per tool; missing, extra, wrongly typed, or unsafe values return
  `{"error": "Invalid tool arguments"}`. The data directory and thresholds are application context and cannot be
  set through tool arguments.
- Maximum 3 tool calls per question (`ToolSession`); rejected calls also count, so loops cannot run away.
- Unknown and malformed territory IDs fail closed — no data is invented.
- Change requests are labelled `PENDING_HUMAN_APPROVAL`, `executed: false`, `applied_to_source_data: false`,
  `simulation: true`. The current quota in a draft is looked up from the CSV, not taken from the caller. Drafts
  created by the advisor are saved only when a person clicks **Save to review queue** (server-side one-time token).
- Demo review actions are limited to **Mark reviewed** and **Return for revision**, each requiring an audit note.
  They update only `local_state/change_requests.json`. SHA-256 fingerprints of every CSV are re-checked and shown.
- Individual performance rankings and employment decisions are refused before any tool or model is called. Rep
  data is only shown in aggregate.
- All numeric claims must be traceable to tool output; the grounding check runs on every advisor answer, and an
  optional-model answer with an untraceable figure is discarded in favour of the baseline answer.
- The audit log stores event type, tool name, safe arguments (IDs and numbers; free text summarised as length),
  status, and timestamp — never the question text, secrets, or API keys.
- Label assumptions and unknowns; `list_data_gaps` states what the dataset does not contain.

## Why not let the model calculate everything?
Arithmetic and business rules should be deterministic and testable. The model's role (if one is added) is to choose
relevant tools, interpret findings, ask clarifying questions, and communicate trade-offs. The baseline proves the
workflow is useful and safe without any model.

## Reuse for a fintech demo
Replace `data/*.csv` and `analysis_tools.py` with fictional policy/control documents and domain tools, register the
new tools in `TOOLS`, add intents to the router, and add domain cases to `tests/evaluation_cases.csv`. The
allowlist, call limit, grounding check, review store, audit log, and Evaluation Lab are domain-neutral.
