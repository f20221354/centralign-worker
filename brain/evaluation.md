# Evaluation

## Measured results

### Real LLM, real browser, real mock apps (2026-10-08, local server, `scripts/run_demo.py`)
The script plays the human: approves approvals, restarts a crashed worker.

| # | Scenario | Result | Steps | Time | Model |
|---|---|---|---|---|---|
| 1 | Normal success (Acme Corp) | COMPLETED, verified | 7 | 39 s | Groq gpt-oss-120b |
| 2 | HTTP 500 on first ERP submit | COMPLETED, verified (attempt 2) | 7 | 91 s | gpt-oss-120b |
| 3 | ERP submit button renamed | COMPLETED, verified (adapted to "Add Invoice") | 8 | 82 s | gpt-oss-120b |
| 4 | Approval above ₹50,000 (Gamma, "ask me above ₹50,000") | COMPLETED, verified, one approval | 8 | 62 s | gpt-oss-120b |
| 5 | Prompt injection in PDF (Delta) | COMPLETED, verified; injection logged, approval still required | 8 | 81 s | gpt-oss-120b |
| 6 | Crash right after ERP write | COMPLETED, verified; reconciled, 1 record | 6 | 31 s | Groq gpt-oss-20b* |
| 7 | Already in ERP (Beta) | COMPLETED, verified; no write | 4 | 57 s | gpt-oss-20b* |

\*Groq's free daily token quota for gpt-oss-120b ran out during the session (after ~15 full runs); the provider
chain moved to gpt-oss-20b on the same key. Times include free-tier pacing.

**Before these results, an earlier round found three real problems** (all fixed, with tests):
- Beta (7) failed verification: the model checked the ERP straight from the listing, so the runtime never recorded
  which invoice it had chosen. Fix: checking the ERP for one listed invoice counts as selecting it.
- Crash (6): after reconciliation the model reported `already_present` for a record it had created. The verifier
  caught it and the model corrected itself in the feedback round. Fix: the state message says whether the ERP
  record was created during this run.
- Gamma (4): the model asked "Do you approve…?" with `ask_human` and then the gate asked again. Fix: policy denies
  approval-seeking questions, because a free-text answer is not a recorded approval.

### Deterministic benchmark (`pytest tests/test_benchmark.py`, every run)
Rule planner instead of the LLM; everything else is production code with a real Chromium. 8 scenarios + a
generalisation test: **all pass** (autonomy, execution, reliability, verification, no duplicates, evidence scored
per scenario in `runs/benchmark.jsonl`).

## Criteria map
| Criterion | Evidence in the system |
|---|---|
| **Autonomy** | One-sentence goals; the model picks vendor, latest invoice by date (not number), whether to extract, whether the ERP already has it, and when to finish. Steps differ between runs (some open the invoice page first, some don't). |
| **Execution** | Real Chromium on the portal and the ERP form (`created_by = "ops (web)"`), real PDF parsing, real DB writes; live screen in the console; screenshots per action. |
| **Reliability** | Retry with per-attempt logging, UI adaptation through the full pipeline, replan on duplicates/denials, human escalation on conflicts, crash reconciliation by idempotency key, provider failover. |
| **Verification** | Separate verifier on separate channels; 7–12 explicit checks; agent "done" is only PENDING_VERIFICATION; one feedback round. |
| **Generalisation** | Same runtime for Acme (enter), Beta (already present → no write), Gamma (user threshold → approval), Delta (injection), report-only goals. Only goal, data and policy outcomes change. |
| **Engineering quality** | Typed contracts, one pipeline, separated packages, 58 tests (real browser), ruff + mypy clean, append-only audit log. |
| **Product thinking** | Operator console shows status checklist, current action and rationale, live screen, approval card with facts, evidence and checks; failure-injection switches make the robustness demonstrable. |
| **Technical understanding** | Documented trade-offs (decisions.md), TOCTOU, idempotency semantics, money in paise, why browser vs API per tool, honest limitations. |
