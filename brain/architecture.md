# Architecture

```
 Browser (evaluator / operator)
   │  https://<vercel>/            landing page (static, Vercel)
   │  https://<vercel>/console     ─┐ Vercel rewrites /console /api /portal /erp
   ▼                                ▼
 ┌──────────────────────── CentrAlign container (Render, Docker) ─────────────────────────┐
 │ FastAPI  src/main.py                                                                    │
 │   /console  operator console (src/console.html)      /api  start · poll · approve · answer · resume
 │   /portal   SupplyHub vendor portal  (mock_apps/vendor_portal, separate app)            │
 │   /erp      Northwind ERP            (mock_apps/internal_erp, separate app + own DB)    │
 │                                                                                         │
 │ Run thread ── Engine (src/runtime/engine.py) ─────────────────────────────────────────  │
 │   Agent (src/agent) ── LLM: goal → GoalSpec · state → ActionProposal · PDF text → fields │
 │      │ raw proposal                                                                     │
 │      ▼                                                                                  │
 │   Validation (src/contracts.py, Pydantic)  ── reject → VALIDATION_ERROR, replan          │
 │      ▼                                                                                  │
 │   Policy gate (src/runtime/policy.py) ── DENY → POLICY_BLOCKED · REQUIRE_APPROVAL → pause │
 │      │ ALLOW (+ live re-check if an approval is used)                                   │
 │      ▼                                                                                  │
 │   Executors (src/execution): browser (Playwright) · api (httpx) · file (pypdf)           │
 │      ▼                                                                                  │
 │   Observation → Classifier (src/runtime/classifier.py) → RETRY/ADAPT/REPLAN/HUMAN/FAIL   │
 │      ▼                                                                                  │
 │   State update (src/runtime/state.py) → TaskState + event log (src/trace.py, SQLite)     │
 │      ▼  … next decision …   finish → PENDING_VERIFICATION                               │
 │   Verifier (src/verification/verifier.py): portal API + ERP API → COMPLETED / FAILED     │
 └─────────────────────────────────────────────────────────────────────────────────────────┘
                          │ LLM calls: Groq → Groq small → Gemini → OpenAI
```

## Request flow (task A)
1. `POST /api/runs` resets demo data, applies failure-injection switches, creates the run (`GOAL_CREATED`) and
   starts a thread.
2. `Engine.drive`: the agent turns the sentence into a `GoalSpec` (`GOAL_UNDERSTOOD`); the ERP's current ids are
   recorded as the baseline for "no unexpected writes".
3. Loop: the agent sees the state and proposes one tool call. Typically: `search_vendor` (browser) →
   `list_invoices` (browser) → `extract_invoice` (file + LLM, cross-checked with the portal API) →
   `search_internal_invoices` (API) → `create_internal_invoice` (browser form) → `finish`.
4. `finish` → verifier → `COMPLETED` with evidence, or one replan round, then `FAILED`.
5. Waiting for a human ends the thread. `POST /api/approvals/{id}` or `/api/questions/{id}` records the decision
   and starts a new drive from persisted state (the same code path as crash recovery).

## Why browser for some tools and API for others
| Tool | Channel | Why |
|---|---|---|
| search_vendor, list_invoices, open_invoice | browser | the portal is a UI for people; shows computer use |
| create_internal_invoice | browser | the ERP's write path for operators is its form; UI changes and errors are real |
| extract_invoice | file (PDF download via API) + LLM | the PDF is the source document |
| search_internal_invoices | API | an exact lookup must not depend on scraping |
| verifier, approval re-checks, reconciliation | API | independent channel from the one the worker wrote through |

## Separation of concerns
`src/agent` knows prompts and tool schemas, nothing about execution. `src/runtime` knows sequencing, policy and
classification, nothing about prompts. `src/execution` performs actions and reports observations. `src/verification`
reads only source systems. `mock_apps` know nothing about the worker.
