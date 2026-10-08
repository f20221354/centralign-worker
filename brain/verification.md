# Independent verification (src/verification/verifier.py)

The agent's `finish` moves the run to `PENDING_VERIFICATION`. The verifier then decides, using only the source
systems:

1. **Vendor**: the GoalSpec's vendor, looked up by exact name in the portal API (not the agent's vendor_id).
2. **Target**: the latest invoice by date, recomputed from the portal API.
3. **Worker processed the latest invoice**: compares with `selected_invoice_id`.
4. **ERP state** through the ERP's JSON API (a different channel from the browser form the worker used):
   exactly one record; vendor, invoice number, amount (paise), due date and invoice date equal the source.
5. **No unexpected writes**: ERP ids now minus the baseline taken when the run started may contain only the
   target record.
6. **Approval on record** if the record was created in this run and the amount is above the threshold.
7. **The agent's claim matches**: `entered` iff the record was created during this run; `already_present` iff it
   existed before. For `report_only` goals: no writes at all, and the answer matches the ERP.

All checks pass → `COMPLETED` + `RUN_COMPLETED` with evidence (vendor, invoice, dates, amount, ERP record and
link, tool executions, approval status). Otherwise the failed checks go back to the agent once; a second failure
is `FAILED`.

Tests (`tests/test_verification.py`) cover a claim with no record, a transposed amount, the wrong invoice, an
extra write, a missing approval and a misreported outcome. During development the verifier caught a real bug:
after a crash, the model reported `already_present` for an invoice it had itself created; it was sent back and
corrected (and the state message now says whether a record was created during the run).
