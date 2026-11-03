# Norbae AI — Annual Sales Planning Advisor

> **Synthetic demo data.** All territories, reps, accounts, and quotas are fictional. This is a portfolio
> prototype, not a production planning system. No savings, accuracy, or client results are claimed.

*Norbae AI helps businesses improve complex workflows with practical AI, measurable evaluation, and human oversight.*

A local dashboard that helps a sales planner:

- compare the company target with the proposed quota total;
- find territory-level planning exceptions using explicit, **illustrative** rules;
- inspect the source data and calculation logic for any territory;
- ask plain-language questions — answered by a **no-LLM baseline** (rule-based intent router + deterministic tools);
- draft a change request that stays `PENDING_HUMAN_APPROVAL` and is **never applied**;
- run an Evaluation Lab that separates automated checks from cases that need human evaluation.

Nothing in the app can change quota, territory, CRM, or source-of-truth data.

## Requirements
- **Python 3.10 or newer.** That's all — the app and tests use only the Python standard library.
  No `pip install`, no API keys, no accounts, no internet connection needed.
- A modern browser (Edge, Chrome, Firefox, Safari).

## Run the dashboard
### Option A — zero-install dashboard (Python only)
From this folder:

```bash
python server.py --open        # starts http://127.0.0.1:8501 and opens your browser
```

Windows: double-click `run_app.bat`, or run `py server.py --open` in PowerShell.
Use another port with `python server.py --port 8600`. Stop with **Ctrl+C**.
The server binds to `127.0.0.1` only (not reachable from other machines).

### Option B — FY27 Planning Cockpit in Streamlit (free, one-time install) — **recommended**
The Advisor-first planning cockpit (Phase 1): chat with the Planning Advisor on the left, your business on the right.
Pick who you are ("Viewing as" — VP, a division leader or an area manager; data is limited to that scope), then:
1. **My business** — BU → division → area → territory → account, drill by clicking or by asking.
2. **Accounts & owners** — 800 fictional accounts with owner, territory, motion, revenue, pipeline, signals.
3. **Plan moves** — move accounts between territories/sellers; the Advisor gives a verdict with reasons
   (motion fit, late-stage pipeline, trend, market signals, capacity, approvals). You apply it with a reason.
4. **Territory purity** — finds Deep/Expansion/Greenfield mixing and suggests fixes.
Steps 5–8 (hierarchy & comp, revenue lock with December estimate, quota model, seller view & disputes) arrive in
Phases 2–3. The classic 7-tab demo is still available: `streamlit run streamlit_classic.py`. Streamlit is free and open source (Apache 2.0).

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows (PowerShell: .venv\Scripts\Activate.ps1)   macOS/Linux: source .venv/bin/activate
pip install -r requirements-streamlit.txt
streamlit run streamlit_app.py  # opens http://localhost:8501 (FY27 cockpit)
```
Windows shortcut: double-click `run_streamlit.bat` (creates the virtual environment on first run).
Both front ends share `local_state/`, so drafts created in one appear in the other.

### Tabs
| Tab | What it does |
|---|---|
| Overview | Target vs. proposed totals, gap, territory and exception counts, variance chart, data validation, data-integrity check |
| Planning Exceptions | Filterable table, configurable illustrative thresholds, territory detail (source rows, calculation steps, rule evaluation, aggregate roster, pipeline sample), cross-file data checks |
| Ask the Advisor | Example questions, no-LLM answers with tool trace and grounding check; save advisor drafts to the review queue |
| Scenario Review | Draft change requests with optional what-if preview; demo-only **Mark reviewed** / **Return for revision** with audit notes |
| Evaluation Lab | Runs `tests/evaluation_cases.csv` checks and the full unit-test suite; record manual evaluation results |
| Architecture & Governance | Workflow, boundaries, tool allowlist, prohibited actions, local audit log |
| Case Study | Portfolio write-up with live figures and only actually-recorded test results; Markdown download |

## Run the tests
```bash
python -m unittest discover -s tests -v     # 105 automated tests (standard library; 1 skips unless Streamlit is installed)
python src/evaluation.py                    # Evaluation Lab cases from the command line
python src/analysis_tools.py                # raw JSON from the deterministic tools (starter-kit command)
```
Windows: `run_tests.bat`. If you have pytest installed, `python -m pytest -q` also works.

## Example questions (no-LLM mode)
- Summarize the plan and tell me whether proposed quotas match the company target.
- Which territories should we review first, and why?
- Show me the details for territory T05.
- What information is missing before I can recommend a quota change?
- Draft a proposed review request for territory T05, but do not apply it.

Also supported: "What if T05 were $13.65M?", "Compare T01 and T02". Unsupported questions get an honest fallback;
requests to rank or fire individual reps, or to change systems, are declined before any tool is called.

## Optional local model (off by default)
The baseline needs no model. If you want to experiment, install [Ollama](https://ollama.com) (free, runs locally),
pull a model, copy `.env.example` to `.env`, and set `NORBAE_LLM_PROVIDER=ollama` and `NORBAE_OLLAMA_MODEL`.
The model can only call allowlisted read-only tools (max 3), cannot create or apply changes, and any answer with a
figure not found in tool output is replaced by the baseline answer. **This adapter has only been tested with a fake
model in the automated tests, not with a real model.**

## Where things are stored
- `data/` — synthetic CSVs (read-only; never written by the app).
- `local_state/` — created on first use; draft requests, demo review decisions, manual evaluation records, last
  evaluation/test runs, and `audit_log.jsonl`. Gitignored. Delete the folder to reset the demo.

## Project layout
See `BUILD_NOTES.md` for the full file tree, decisions, test results, and limitations, and `docs/architecture.md`
for the architecture and guardrails. Screenshots are in `docs/screenshots/`; sample outputs in
`docs/sample_output.md`.

## Portfolio integrity
Thresholds are illustrative demo thresholds requiring business-owner validation. A flag means "review this
input", not "this quota is wrong". Historical revenue per rep is not a quota-capacity model. Do not describe this
prototype as production-ready or claim measured outcomes.
