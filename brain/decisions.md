# Decisions

| # | Decision | Alternatives | Why |
|---|---|---|---|
| D1 | Model proposes one tool call at a time from a state summary | full chat history; plan-then-execute | bounded tokens, survives restarts, each step reacts to the last observation |
| D2 | Policy in plain Python | LLM judge; prompt rules | deterministic, testable, immune to injection |
| D3 | Approval = server-side snapshot + live recheck | boolean flag; trust the model | no self-approval; TOCTOU-safe |
| D4 | Waiting for a human ends the thread; decisions start a new drive | blocking thread | the same resume path as crash recovery; nothing held in memory |
| D5 | Browser for portal reads and ERP writes; API for lookups and verification | all browser; all API | shows computer use where it matters; exact where exactness matters; verifier on a separate channel |
| D6 | Idempotency key = business identity (vendor + invoice number) | random per attempt | retries, crashes and other submitters converge on one record |
| D7 | Integer paise | float; Decimal everywhere | exact, cheap, DB-friendly |
| D8 | Verifier recomputes the target from source systems | trust the agent's selection | catches wrong-invoice and claim errors (it did, live) |
| D9 | One FastAPI process mounting the two mock apps | separate services | one container to deploy; apps remain separate code with separate storage |
| D10 | Static HTML console and landing page | Next.js / Streamlit | no build step, fast on phones, nothing to break in the demo |
| D11 | Groq → Groq small → Gemini → OpenAI chain | one provider | free tiers run out (observed); quotas are per model |
| D12 | One verification feedback round | none; unlimited | fixes honest mistakes without loops |
| D13 | Deterministic planner in tests | mock the LLM response text | tests the real pipeline quickly and repeatably |
