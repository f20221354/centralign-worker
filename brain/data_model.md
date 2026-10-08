# Data model (src/contracts.py)

All models are Pydantic with `extra="forbid"`. Money is `int` paise (`to_paise` parses via `Decimal`).

## GoalSpec
`objective`, `vendor_name`, `invoice_selector="latest"`, `mode` (`enter_if_missing` | `report_only`),
`fields_to_extract`, `success_criteria`, `constraints`, `approval_threshold_paise` (user may only tighten).

## ActionProposal
`tool_name` (Literal of the 8 registered tools), `arguments` (validated against the tool's model),
`action_rationale` (≤ 300 chars).

## Tool argument models
`SearchVendorArgs(query)`, `ListInvoicesArgs(vendor_id ^V-…)`, `OpenInvoiceArgs` / `ExtractInvoiceArgs(vendor_id,
invoice_number)`, `SearchInternalInvoicesArgs(vendor_name, invoice_number?)`, `CreateInternalInvoiceArgs(vendor_name,
invoice_number, invoice_date, due_date ≥ invoice_date, amount_inr (≤ 2 decimals, > 0), po_number?, ui_hints?)`,
`AskHumanArgs(question)`, `FinishArgs(outcome ∈ entered|already_present|not_entered, summary)`.
`CreateInternalInvoiceArgs.business_fields()` is what approvals and idempotency are about; `ui_hints` is excluded.

## Observation
`action_name`, `status` (SUCCESS, HTTP_ERROR, DOM_ERROR, DATA_AMBIGUITY, POLICY_BLOCKED, VALIDATION_ERROR, CONFLICT,
NOT_FOUND), `data`, `error_message`, `screenshot_path`, `http_status`, `channel` (browser/api/file/runtime).

## TaskState (persisted as JSON in `runs.state_json`)
`run_id, goal_text, goal_spec, vendor_id, vendor_name, candidate_invoices, selected_invoice_id,
source_invoice_data, erp_record, erp_baseline_ids, executed_steps[StepRecord], hints, human_answers,
injection_warnings, pending_action, pending_approval_id, pending_question_id, adaptations, step_index, status,
is_terminal, final_summary, finish_outcome, verification, verify_rounds, scenario`.

Status: PLANNING → RUNNING ⇄ WAITING_APPROVAL / WAITING_HUMAN → PENDING_VERIFICATION → COMPLETED | FAILED;
INTERRUPTED when the worker died (resume reconciles).

## ApprovalSnapshot (table `approvals`)
`approval_id, run_id, action_id, tool_name, parameters (business fields), critical_state_facts (live portal +
ERP facts), policy_version, state_payload_hash (sha256 of the facts), requested_at, status (PENDING, APPROVED,
REJECTED, CONSUMED, STALE), decided_at, note`.

## ExecutionEvent (table `events`, append-only via triggers)
`run_id, step_index, attempt_index, event_type, payload, timestamp`. Event types: GOAL_CREATED, GOAL_UNDERSTOOD,
ACTION_PROPOSED, ACTION_REJECTED, POLICY_EVALUATED, APPROVAL_REQUESTED, APPROVAL_GRANTED, APPROVAL_REJECTED,
APPROVAL_REVALIDATION, APPROVAL_INVALIDATED, CLARIFICATION_REQUESTED, CLARIFICATION_RESOLVED, ACTION_STARTED,
OBSERVATION_RECORDED, OUTCOME_CLASSIFIED, RECOVERY_ATTEMPTED, REPLAN_REQUIRED, REPLANNED, INVOICE_SELECTED,
PROMPT_INJECTION_DETECTED, ACTION_COMPLETED, WORKER_CRASHED, WORKER_RESUMED, ACTION_RECONCILED,
SIDE_EFFECT_NOT_FOUND, AGENT_REPORTED_DONE, VERIFICATION_COMPLETED, RUN_COMPLETED, RUN_FAILED.

## ERP tables (mock_apps/internal_erp)
`invoices(id, vendor_name, vendor_key, invoice_number, invoice_date, due_date, amount_paise BIGINT, po_number,
idempotency_key UNIQUE, created_by, created_at, UNIQUE(vendor_key, invoice_number))`, `settings` (failure
injection), `sessions` (UI login).
