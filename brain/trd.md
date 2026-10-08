# TRD

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.12 | typed (mypy clean), linted (ruff) |
| Contracts | Pydantic v2 | `extra="forbid"` everywhere; tool schemas generated from the models |
| Server | FastAPI + Uvicorn | one process serves console, API and both mock apps (mounted) |
| Browser automation | Playwright (Chromium) | headless by default; `HEADED=1`, `RECORD_DIR` for video |
| Mock vendor portal | FastAPI app, in-memory data, ReportLab PDFs | HTML UI + JSON API + admin amend/reset |
| Mock ERP | FastAPI + SQLAlchemy Core | SQLite by default, Postgres via `ERP_DATABASE_URL`; unique constraints |
| Worker state | SQLite (`runs/worker.db`) | runs, append-only events (triggers), approvals, questions |
| LLM | OpenAI-compatible chat + tool calling | Groq `openai/gpt-oss-120b` → Groq `openai/gpt-oss-20b` → Gemini 2.5 Flash → OpenAI `gpt-4.1-mini` |
| PDF | pypdf text layer | scanned PDFs out of scope |
| Front end | static HTML (console + landing page) | no build step; works on phones |
| Deploy | Vercel (landing page + rewrites) + Render (Docker worker) | see deployment.md |
| Tests | pytest | 58 tests, real browser against real mock apps |

## Runtime parameters
`MAX_STEPS=24`, `MAX_ATTEMPTS=3` per action (transient), `MAX_ADAPTATIONS=2` per run, backoff 0.8 s × 2ⁿ (≤ 5 s),
one verification feedback round, `RUNS_PER_HOUR=20` on the public demo, one run at a time.

## LLM integration
- Each decision is one chat call: system prompt + a state message rebuilt from `TaskState` (goal spec, vendor,
  candidates, selection, extracted data, ERP record with origin, human answers, last 8 steps, last 4 runtime
  notes) + the tool schemas. History is not replayed, which bounds tokens (~1.5K input per call).
- The model must return a tool call; `action_rationale` is a required argument of every tool.
- Goal understanding and PDF extraction are JSON calls validated by Pydantic / the runtime.
- Provider failover: per-minute 429s are waited out (≤ 30 s); daily-quota or billing 429s put that provider on a
  1-hour cooldown; network/5xx fail over immediately. The model behind each decision is logged.
