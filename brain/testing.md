# Testing

```bash
.venv/bin/python -m pytest          # 58 tests, ~20 s, real Chromium against the real mock apps
.venv/bin/ruff check .              # lint
.venv/bin/mypy src mock_apps        # types
RUN_LLM_BENCHMARK=1 .venv/bin/python -m pytest tests/test_benchmark.py::test_llm_benchmark   # needs a running server + keys
```

The harness (`tests/conftest.py`) starts the real server on a random port with a temp ERP database and run store,
and replaces only the LLM with a deterministic `Planner` that reads the same TaskState. Everything after the
proposal is production code.

| File | Covers |
|---|---|
| test_policy.py | allow/deny/approval, threshold edge (exactly ₹50,000), user can only tighten, fingerprints (ui_hints excluded), rejected stays denied, latest-only, source-data match, duplicates, report-only, wrong vendor, ask_human-as-approval, revalidation (facts changed, record appeared, policy version) |
| test_security.py | unknown tools, extra/malformed args, schemas, paise arithmetic, append-only events, self-approval attempt rejected before policy, unregistered tool mid-run |
| test_idempotency.py | identical resubmit → exists, conflict → 409, vendor-name normalisation, key reuse conflict, 8 concurrent submits → 1 row, 422 validation |
| test_recovery.py | classifier routes, happy path via browser, HTTP 500 retry with per-attempt events, UI adaptation re-checked by policy, crash reconciliation (one write), unrecorded-but-not-written action redone |
| test_verification.py | no record, wrong amount, wrong invoice, extra write, missing approval, misreported outcome, verification feedback round |
| test_prompt_injection.py | detection, no write while approval pending, rejection is final, approved path with live recheck, TOCTOU amount change, TOCTOU conflicting record |
| test_benchmark.py | the scenarios scored per criterion, generalisation across vendors, optional LLM tier |
