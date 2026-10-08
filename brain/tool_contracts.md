# Tool contracts (src/agent/tools.py + src/contracts.py)

The model sees exactly these 8 tools (OpenAI function schemas generated from the Pydantic models, plus a required
`action_rationale`). Anything else fails validation and is logged as `ACTION_REJECTED`.

| Tool | Arguments | Channel | Success data | Typical failures |
|---|---|---|---|---|
| `search_vendor` | `query` (2–80) | browser: portal search form | `matches[{vendor_id, name, city}]` | NOT_FOUND (no match), HTTP_ERROR (timeout) |
| `list_invoices` | `vendor_id` (`V-…`) | browser: vendor page table | `vendor_name`, `invoices[{invoice_number, invoice_date, due_date, amount, status}]` | NOT_FOUND (404) |
| `open_invoice` | `vendor_id`, `invoice_number` | browser: invoice page | `fields{…}` | NOT_FOUND |
| `extract_invoice` | `vendor_id`, `invoice_number` | file: PDF via portal API, text via pypdf, fields via LLM | `invoice{vendor_name, invoice_number, invoice_date, due_date, amount_paise, amount_inr, po_number}`, `pdf`, `injection_signals`, `cross_check` | DATA_AMBIGUITY (PDF ≠ portal), VALIDATION_ERROR (unusable extraction) |
| `search_internal_invoices` | `vendor_name`, `invoice_number?` | API: ERP | `matches[…]`, `count` | HTTP_ERROR |
| `create_internal_invoice` | see data_model; `ui_hints.submit_button_label?` | browser: ERP New-invoice form, with idempotency key | `erp_outcome` (created/exists), `erp_url`, `record` | HTTP_ERROR (500), DOM_ERROR (button missing, returns `available_buttons`), VALIDATION_ERROR (422), CONFLICT (409) |
| `ask_human` | `question` | runtime: pauses the run | answer appended to `human_answers` | policy DENY if it asks for approval |
| `finish` | `outcome`, `summary` | runtime: PENDING_VERIFICATION → verifier | verification result | verification failure → one replan round |

Validation highlights: dates must be ISO; `due_date ≥ invoice_date`; amounts positive with ≤ 2 decimals;
invoice numbers match `^[A-Za-z0-9][A-Za-z0-9/_-]{1,30}$`; no extra fields.

Each executor returns an `Observation`; none decides whether the goal is met.
