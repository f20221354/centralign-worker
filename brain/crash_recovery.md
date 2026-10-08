# Crash recovery

**What is persisted**: TaskState after every step and *before* every side-effect attempt (so the intent is on disk),
the append-only event log, approvals and questions.

**Simulated crash**: with "Worker crashes right after the ERP write", `create_internal_invoice` submits the form,
the ERP commits, and then the worker dies (`SimulatedCrash`, or `os._exit(1)` with `CRASH_HARD=1`) before the
observation is recorded. The supervisor records `WORKER_CRASHED` and marks the run `INTERRUPTED`. A server restart
does the same for any run left RUNNING.

**Restart** (`POST /api/runs/{id}/resume`, the console's "Restart worker" button):
1. `WORKER_RESUMED`; find every side-effect `ACTION_STARTED` without a matching `OBSERVATION_RECORDED`.
2. For each, query the ERP API by idempotency key.
   - Found → `ACTION_RECONCILED`: the record is put in state, any approval used is CONSUMED, the model is told the
     write happened in this run. No second write.
   - Not found → `SIDE_EFFECT_NOT_FOUND`: safe to propose again (through the normal pipeline).
3. Continue the loop; the verifier confirms one record and outcome `entered`.

Tests: `test_crash_after_write_is_reconciled_without_duplicate` (exactly one side-effect attempt, one ERP row) and
`test_unrecorded_action_that_never_reached_erp_is_safe_to_redo`.
