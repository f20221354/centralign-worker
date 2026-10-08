# Limitations (not production-ready)

- **Narrow workflow**: one task family (latest vendor invoice → ERP). Batches, credit notes, partial matches and
  multi-currency are out of scope.
- **Mock systems**: the portal and ERP are mine; real portals have logins, CAPTCHAs, pagination and messier HTML.
  The executor uses accessible labels and can adapt a renamed button, not an arbitrary redesign.
- **Text PDFs only**: scanned invoices would need OCR or a vision model.
- **Single worker, SQLite**: one run at a time; state in the container's `/tmp` on the free deployment, so a restart
  resets the demo. Production would use Postgres, a queue and leases for the crash supervisor.
- **Approval UX**: in-console only; no email/Slack, no roles, no auth on the public demo beyond a rate limit.
- **LLM dependence**: free tiers are slow and limited (Groq: 200K tokens/day per model, about 15 full runs); smaller
  models are less reliable. The gate and verifier stop bad writes, not wasted steps.
- **Verifier scope**: it trusts the portal's structured data as the source of truth; it does not re-OCR the PDF.
- **Crash simulation**: in-process (`SimulatedCrash`) in the console; `CRASH_HARD=1` kills the process for real.
- **Security**: no secret manager, no per-user authZ, no tamper-evident hashing of the event log (append-only only).
