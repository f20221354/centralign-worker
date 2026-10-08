# Idempotency

- **Key**: `sha256(normalised vendor | invoice number)[:24]`, computed by the runtime, sent in the ERP form's
  "Reference key" field. The same business invoice always has the same key, whoever submits it.
- **Database**: `UNIQUE(vendor_key, invoice_number)` and `UNIQUE(idempotency_key)`; `vendor_key` is lower-cased
  and whitespace-normalised, so "ACME  corp" is the same vendor.
- **Semantics**: identical resubmission → 200 `exists` (idempotent success, the record is returned). Same invoice id
  or key with different financial data → 409 `conflict` with the differing fields (never silently accepted).
  Concurrent identical submits → the constraint decides; exactly one 201 (test with 8 threads).
- **Worker side**: retries reuse the key; the policy denies creating an invoice already known to be in the ERP;
  reconciliation after a crash looks the key up before anything else.
