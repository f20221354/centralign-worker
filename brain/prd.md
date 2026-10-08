# PRD

## User problem
Accounts-payable staff spend their day copying invoices from vendor portals into the ERP: find the vendor, find
the right invoice, read the PDF, check the ERP, type it in, double-check it. It is boring, error-prone (wrong
invoice, transposed digits, duplicates) and interrupt-driven (portal down, ERP form changed, approvals).

## User story
As an AP specialist, I give the worker a goal in plain language and come back to either a verified result with
evidence, or a precise question that only I can answer. I approve large amounts myself, and I can always see
what the worker did and why.

## Success criteria
- Accepts natural-language goals; no step-by-step instructions needed.
- Picks the right vendor and the latest invoice by date.
- Extracts amount, due date and other fields from the PDF and cross-checks them against the portal.
- Enters the invoice in the ERP through its web form, or recognises it is already there.
- Recovers from transient errors, UI changes, duplicates and crashes without double entry.
- Pauses for approval above ₹50,000 (or a stricter user-given limit) and re-checks facts after approval.
- Ignores instructions embedded in documents.
- Reports completion only after independent verification, with evidence.

## Demo scenarios
1 normal success · 2 HTTP 500 retry · 3 renamed button adaptation · 4 human approval · 5 prompt injection ·
6 crash after write + idempotent recovery · 7 already in ERP (replan, no write) · plus the three generalisation
tasks (Acme / Beta / Gamma with "ask me above ₹50,000").

## Non-goals
Payment execution, vendor onboarding, GST reconciliation, multi-invoice batches, scanned PDFs.
