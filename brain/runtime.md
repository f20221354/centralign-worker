# Runtime (src/runtime/engine.py)

## Entry points
- `start(goal, scenario)` → creates the TaskState, `GOAL_CREATED`.
- `drive(run_id)` → reconcile if INTERRUPTED/RUNNING → understand if PLANNING → handle a decided approval or an
  answered question → verify if PENDING_VERIFICATION → loop. Returns when terminal or waiting for a human.
  Any exception ends in a recorded `RUN_FAILED`; `SimulatedCrash` propagates like a dead process.
- `decide(approval_id, approve)` and `answer(question_id, text)` are called by the console (a human), never by
  the model.

## The pipeline (`_process`)
```
ACTION_PROPOSED
  └ validation (ActionProposal + tool model) ── fail → ACTION_REJECTED, hint, REPLAN
  └ policy.evaluate ── ALLOW with approval_id → APPROVAL_REVALIDATION (live facts) → policy.revalidate
                    ── DENY → POLICY_BLOCKED, hint, REPLAN
                    ── REQUIRE_APPROVAL → ApprovalSnapshot, APPROVAL_REQUESTED, WAITING_APPROVAL (thread ends)
  └ ask_human → CLARIFICATION_REQUESTED, WAITING_HUMAN
  └ finish → AGENT_REPORTED_DONE, PENDING_VERIFICATION → verifier
  └ execute with recovery:
       for attempt: ACTION_STARTED (state saved first if side effect) → executor → OBSERVATION_RECORDED
                    → OUTCOME_CLASSIFIED → RETRY? RECOVERY_ATTEMPTED + backoff : break
       state.apply (INVOICE_SELECTED, REPLANNED) → StepRecord → ACTION_COMPLETED (consumes approval)
       ADAPT → hint with the controls on the page; the model proposes the adapted action → _process again
       REPLAN → hint; HUMAN → question; FAIL → RUN_FAILED
```

## What the model sees each step
`prompts.state_message`: goal + GoalSpec, vendor, portal invoices seen, selected invoice, extracted data,
injection warning, ERP record and whether it was created during this run, human answers, the last 8 steps with
short observations, the last 4 runtime notes. No secrets, no credentials, no raw HTML.

## Budgets
24 steps, 3 attempts per transient action, 2 UI adaptations per run, 1 verification feedback round,
2 consecutive non-tool replies.
