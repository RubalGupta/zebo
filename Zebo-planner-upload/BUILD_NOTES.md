# Build notes — Norbae AI Annual Sales Planning Advisor

Build date: 2026-10-14. Everything below reflects commands actually run in the build environment
(Linux container, Python 3.13; test suite also run on 3.11 and 3.12). It was **not** run on Windows.

## 1. What was built
A local, zero-dependency dashboard and agent-tool architecture on top of the starter kit:
- Hardened deterministic tools (validation, configurable illustrative thresholds, boundary-safe comparisons,
  cross-file consistency checks, data-gap listing, hypothetical what-if, SHA-256 source fingerprints).
- Allowlisted, argument-validated, 3-call-bounded tool layer with a local audit log.
- No-LLM baseline advisor (rule-based intent router) with an automated grounding check on every figure.
- Draft-only change requests and a demo-only review queue (Mark reviewed / Return for revision + audit note).
- Evaluation Lab (13 automated cases, 4 manual cases), unit-test runner, case-study generator.
- Optional local-model adapter (Ollama, off by default, localhost only).

## 2. Key implementation decisions
| Decision | Why |
|---|---|
| **Python stdlib web server + static HTML/JS instead of Streamlit** | PyPI was blocked in the build environment, so Streamlit could not be installed or verified. The brief forbids claiming unverified behaviour; a stdlib server runs with zero installs on any Python 3.10+ machine and could be fully exercised here (API tests + headless-browser end-to-end run + screenshots). |
| unittest (stdlib) for tests | Starter kit already used unittest; no install needed. pytest also discovers them. |
| Starter data **unchanged** | SHA-256 of all four CSVs identical to the starter kit. Intentional anomalies preserved (plan total mismatch; T10 exactly on the 5% threshold). |
| Starter functions kept | `summarize_plan()`, `find_planning_exceptions()`, `get_territory_detail(territory_id)`, `run_all()`, `execute_read_only_tool()`, `create_change_request(action, details)` keep their names and starter call signatures; the 6 starter tests pass unmodified. New optional parameters (`data_dir`, `thresholds`) are keyword-only in practice. |
| Flag codes renamed | `quota_variance_at_least_5_percent` → `quota_variance` (etc.) because thresholds are now configurable; a hard-coded "5" in the name would be misleading. Human-readable reasons include the actual threshold. |
| `>=` with tolerance (1e-9) | A variance of exactly 5% (T10) is flagged consistently despite floating point. |
| Zero/negative capacity → `invalid_capacity` flag, per-rep metrics `None` | Surfacing the bad input is more useful than crashing; no division by zero. Non-numeric values raise `DataValidationError` naming file/row/column (no absolute paths). |
| Exception ordering | Deterministic sort (rules triggered, then absolute $ variance). Labelled as a review-queue order, not a judgement. |
| `get_territory_detail` keeps `review_required: True` (starter behaviour) and adds `rule_triggered` | Starter semantics: any change to a territory needs human review. |
| Rep data aggregate-only | No per-rep listing or ranking anywhere in the UI or tools (governance boundary). |
| Advisor drafts saved via one-time server-side token | Client cannot forge a draft with `executed: true`; saving is an explicit human click. |
| Evaluation cases TC11–TC17 appended | New automated baseline cases (grounding, fallback, sensitive request, call limit, arg validation, data integrity, threshold boundary). TC01–TC10 unchanged; TC07–TC10 remain manual. Any case without a registered automated check is shown as "Manual evaluation required" by default. |
| Cross-file reconciliation is informational | Rep-roster revenue does not reconcile to territory historical revenue in any territory (−2.71% to +1.67%). This is reported as a data-quality note, not a planning exception, so it does not change the starter exception count. It was **not** "fixed" in the data. |

## 3. Commands actually run and results
| Command | Result |
|---|---|
| `python -m unittest discover -s tests` on the **original starter kit** | 6 tests, OK |
| `python -m unittest discover -s tests -v` (final, Python 3.13) | **99 tests, OK** (0 failures, 0 errors, 0 skipped) |
| same on Python 3.11 and 3.12 | OK |
| Syntax check with `feature_version=(3,10)` | OK (3.10 interpreter itself was not available) |
| `python src/evaluation.py` | **13/13 automated cases passed; 4 manual cases (TC07–TC10) pending — not run** |
| Mutation check: raised call limit 3→4 | Suite failed as expected (2 failures, 1 error); reverted |
| Dashboard "Run unit test suite" button (server endpoint) | 98 passed, 1 skipped (static-file test intentionally skipped when run from inside the UI to avoid recursion) |
| Headless Chromium end-to-end script (Playwright, build environment only) | All 7 tabs loaded; KPIs $90,000,000 / $91,175,000 / +$1,175,000 / 10 / 2; threshold change to 6% → 1 exception, reset → 2; all 5 example questions answered with grounding "all traceable"; sensitive and unknown questions handled; draft saved, what-if previewed, rationale & audit-note validation shown, review recorded; integrity check "unchanged"; Eval Lab 13/13 + 4 manual; no console errors; mobile width 390px with no horizontal scroll |

Per-file automated test counts: starter `test_planning_advisor` 6 · `test_analysis_tools` 32 · `test_agent` 15 ·
`test_router` 19 · `test_evaluation` 6 · `test_llm_adapter` 8 · `test_server` 13.

### Coverage of the 17 required test areas
| # | Requirement | Where |
|---|---|---|
| 1 | Plan total mismatch | `test_planning_advisor`, `test_analysis_tools.StarterDataTotals`, TC01 |
| 2 | Totals when target = quota | `test_analysis_tools.MatchingTotals` |
| 3 | Exception detection | `StarterDataTotals`, TC02 |
| 4 | Threshold boundaries | `ThresholdBoundaries` (variance ±, ratio, opp. index inclusive), TC17 |
| 5 / 6 | Known / unknown territory | `TerritoryLookup`, TC03, TC04 |
| 7 | Missing CSV / columns / invalid numbers | `MalformedInputs`, `test_server.test_missing_data_returns_useful_error` |
| 8 | Zero/negative capacity | `CapacityAndDivision` |
| 9 | Unsupported tool rejected | `test_agent.Allowlist`, TC05 |
| 10 | Tool argument validation | `test_agent.ArgumentValidation`, TC15 |
| 11 | Tool-call limit | `test_agent.CallLimit`, `test_router` multi-territory, TC14 |
| 12 | Draft remains unexecuted | `test_agent.DraftOnly`, TC06 |
| 13 | Review status doesn't alter source data | `ReviewStoreDoesNotAlterData`, server flow test, TC16 |
| 14 | Router handles example questions | `test_router.ExampleQuestions`, TC11 |
| 15 | Unknown question fallback | `test_router.FallbackAndGuardrails`, TC12 |
| 16 | No hardcoded KPI totals in UI | `test_server.NoHardcodedKpis` (literal scan) + `test_overview_changes_when_csv_changes` (runtime proof) |
| 17 | Manual cases not auto-passed | `test_evaluation` |

### Manual evaluation (not done)
TC07–TC10 are LLM-response tests. No language model was used in this build, so they were **not run** and are shown
as "Manual evaluation required". The Evaluation Lab lets a reviewer record a Pass/Fail with notes; recorded results
are labelled "Manual: Pass/Fail", never "Passed".

## 3b. Streamlit front end (added 2026-10-14)
- `streamlit_app.py` is a second UI over the same `src/` modules (no new business logic). Run with
  `streamlit run streamlit_app.py` after `pip install -r requirements-streamlit.txt` (Streamlit ≥ 1.50; uses
  `width="stretch"`, the current replacement for the deprecated `use_container_width`).
- **Not run with real Streamlit in the build environment** — PyPI is blocked there, so Streamlit could not be
  installed. What was verified: the script compiles, and `tests/test_streamlit_app.py` executes every tab's code
  path against a recording stub of the `streamlit` module (initial render, ask → draft not auto-saved, rationale
  required, draft saved as PENDING/executed false, audit note required, mark reviewed, thresholds, evaluation lab,
  no hard-coded KPIs, CSV fingerprints unchanged). A real headless `AppTest` run is included and runs
  automatically once Streamlit is installed (it is skipped otherwise).
- Full suite after this change: **105 tests, OK (1 skipped: the real-Streamlit AppTest)**.

## 3c. FY27 Planning Cockpit — Phase 1 (added 2026-10-14)
Requested flow: Advisor front and centre → see my business at a high level within my access → accounts, owners,
revenue → move accounts for next year with AI advice → territory purity → (Phase 2) hierarchy & comp, revenue
verification + December estimate → (Phase 3) quota model & allocation, team overrides with AI suggestions, seller
quota/payout view with disputes.

Phase 1 delivered:
- `tools/generate_fy27_data.py` → `data/fy27/` (synthetic, seed 2027): 1 BU, 5 divisions, 34 areas (6–8 per
  division), 156 territories (Deep/Expansion/Greenfield), 350 sellers in 7 roles, 800 accounts, monthly revenue
  FY25 (12 months) and FY26 (Jan–Nov; December deliberately not closed), 1,217 opportunities, fictional industry
  outlook and account signals. 62 accounts are seeded in territories of the wrong motion.
- `src/planning/`: data validation, personas & access scope, rollups (YoY compares Jan–Nov to Jan–Nov), draft FY27
  plan store (audited moves with reason/verdict/who/when, revert in order; never writes data/), explainable move
  advice (motion fit, purity, late-stage pipeline, big opportunity, trend vs coverage change, industry outlook,
  account signal, capacity, cross-area approval → Recommended / Review with caution / Not recommended), purity report
  and fixes. All rules illustrative (`planning/config.py`).
- `src/advisor/`: allowlisted, scope-enforced, argument-validated tools (max 5 per turn, logged); RuleAdvisor with
  drill-down context, entity resolution, rankings, moves/what-if, best-fit, purity, plan changes, guardrails and
  honest "Phase 2/3" answers; rounding-aware grounding check; ClaudeAdvisor (optional, off by default, Claude API
  tool-use loop with code-level guardrails and fallback; tested with a fake client only); `get_advisor` factory.
- `streamlit_app.py`: Advisor-first cockpit (chat left, workspace right, persona selector, 8-step navigation).
  The previous Streamlit app is kept as `streamlit_classic.py`.
- Tests: `test_planning_engine.py`, `test_advisor_v2.py`, `test_streamlit_cockpit.py` (stub run of every step,
  chat drill-down, scope, apply-requires-reason, data unchanged) + real-Streamlit AppTests that run when Streamlit is
  installed. **Full suite: 140 tests, OK (2 skipped: the real-Streamlit AppTests).**
- Not verified here: the cockpit inside real Streamlit (blocked install). Run
  `python -m unittest discover -s tests -v` on a machine with Streamlit to execute the two AppTests.

Phase 2/3 plan (agreed): hierarchy & comp plans; revenue verification with automatic discrepancy flags and a
December estimate (prior-year December share, run rate, December-closing pipeline → low/likely/high), baseline lock
with a January true-up rule; quota model BU → division → area → territory → account (proportional to locked
revenue + growth signals) with team overrides and Advisor suggestions; seller quota & payout view with disputes.

## 3d. Updates (2026-10-14, afternoon)
- Floating 🤖 Advisor (bottom-right popover), Advisor-insights strip on each step, laptop-friendly density,
  `$` no longer rendered as LaTeX (regression test added).
- Norbae AI brand palette from norbae-ai.lovable.app (navy #0a1f44, cyan #22d3ee → mint #10d9a0 gradients,
  blue #2f80ff, dark panel #0a1530, Lexend font).
- FY27 revenue impact (`src/planning/impact.py`, tools `revenue_impact`, `plan_revenue_impact`): revenue that
  changes hands (nets out at company level) + company effect (coverage change, late-stage pipeline disruption)
  with low/likely/high. The Advisor remembers its last suggested moves, so follow-ups like "if I make the change
  how will revenue be impacted for 2027" work. Impact also shows on every move card and for the draft plan.
  All factors illustrative. Full suite: 145 tests, OK (2 skipped: real-Streamlit AppTests).

## 4. Known limitations
- Synthetic data only; thresholds are illustrative and not business-validated.
- The rule-based router understands a limited set of phrasings; anything else gets the honest fallback. Keyword
  rules can misroute unusual wording (e.g. a sentence containing both "rank" and "reps" is always declined).
- The optional Ollama adapter was tested only with a fake model; behaviour with a real model is unverified.
- Not run on Windows in this build. The code uses only cross-platform stdlib calls and `.bat` launchers are provided,
  but this has not been verified on a Windows machine.
- Python 3.10 itself was not available; 3.11/3.12/3.13 were tested.
- No authentication or roles; the server is intended for single-user localhost use.
- Demo review records and the audit log are local JSON files (no concurrency guarantees beyond a process lock).
- Pipeline data is a 60-account sample; coverage ratios are deliberately not computed.
- Threshold edits are per-browser (localStorage) and not persisted server-side.

## 5. Future work
- Run TC07–TC10 with a real (free/local) model and a documented human grading rubric.
- Multi-scenario comparison with owner-approved assumptions; capacity/ramp modelling once data exists.
- Authentication and role-based review if ever used beyond a demo.
- Fintech variant: swap CSVs/tools for fictional policy/control documents; reuse the registry, router, review store,
  and Evaluation Lab.

## 6. File tree
```
Norbae_AI_Annual_Sales_Planning_Advisor/
├── README.md                     setup, run, test
├── BUILD_NOTES.md                this file
├── requirements.txt              (stdlib only — nothing to install)
├── .env.example                  optional local-model settings, no secrets
├── .gitignore
├── run_app.bat / run_tests.bat   Windows launchers
├── server.py                     local JSON API + static server (127.0.0.1)
├── streamlit_app.py              FY27 Planning Cockpit (Phase 1, Advisor-first)
├── streamlit_classic.py          classic 7-tab Streamlit demo
├── tools/generate_fy27_data.py   synthetic FY27 dataset generator
├── data/fy27/                    FY27 synthetic dataset (8 CSVs)
├── src/planning/  src/advisor/   FY27 engine + Advisor
├── requirements-streamlit.txt / run_streamlit.bat / .streamlit/config.toml
├── data/                         synthetic CSVs (unchanged from starter)
│   ├── accounts.csv  quota_plan.csv  sales_reps.csv  territories.csv
├── src/
│   ├── config.py                 thresholds, paths, call limit
│   ├── analysis_tools.py         deterministic read-only tools (starter, extended)
│   ├── agent_blueprint.py        allowlist, validation, ToolSession, create_change_request (starter, extended)
│   ├── intent_router.py          no-LLM baseline advisor + grounding check
│   ├── llm_adapter.py            optional local model (off by default)
│   ├── review_store.py           local demo queue, reviews, audit log
│   ├── evaluation.py             Evaluation Lab runner
│   └── case_study.py             case-study generator
├── web/  index.html  app.css  app.js
├── tests/
│   ├── evaluation_cases.csv      TC01–TC10 (starter) + TC11–TC17
│   ├── test_planning_advisor.py  starter tests (unchanged)
│   ├── test_analysis_tools.py  test_agent.py  test_router.py
│   ├── test_evaluation.py  test_llm_adapter.py  test_server.py  test_streamlit_app.py
│   └── _helpers.py
└── docs/
    ├── architecture.md           architecture & governance
    ├── case_study_template.md    updated template
    ├── case_study_generated.md   export from the app (recorded runs only)
    ├── sample_output.md          captured CLI and advisor outputs
    └── screenshots/              01–08 PNGs from the headless-browser run
```
`local_state/` is created at runtime and is not shipped.
