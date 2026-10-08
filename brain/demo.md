# Demo

## Live
Open the site → **Try live demo** → pick an example (A–D) → optionally tick a failure-injection switch → **Run
worker**. Watch the status checklist, the worker's screen and the trace. Approve when the approval card appears;
press **Restart worker** if you chose the crash switch. Portal and ERP links are in the header (ERP login:
`ops` / `northwind-demo`, synthetic data).

## Script (3–5 minutes)
1. Goal A typed in plain language → GOAL_UNDERSTOOD shows the GoalSpec.
2. The worker's browser searches the portal, lists Acme's invoices, picks INV-1042 by date (INV-1045 is older).
3. Extraction from the PDF, cross-checked with the portal; ERP checked first.
4. Same goal with "ERP returns HTTP 500": failure, classified transient, retried, attempt 2 succeeds.
5. "Renamed submit button": DOM_ERROR, adapted proposal, policy-checked again, success.
6. Goal C (Gamma, ₹75,000): approval card with facts; approve; live re-check; write.
7. Crash after the write: worker dies, restart, ACTION_RECONCILED, no duplicate.
8. Verification table and evidence; the ERP list shows exactly one record per invoice.

## Video production (reproducible)
`scripts/demo_video/record.py` drives the real console with Playwright (recording the console and, through
`RECORD_DIR`, the worker's own browser), then `scripts/demo_video/assemble.py` cuts, speeds up waiting, adds
captions and a voice-over (macOS `say`) and writes an H.264 MP4. See the scripts' docstrings.
