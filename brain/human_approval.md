# Human approval

**When**: `create_internal_invoice` with an amount above `min(₹50,000, user threshold)`. Exactly ₹50,000 does not
require approval.

**Request**: policy returns REQUIRE_APPROVAL → the engine reads live facts (portal invoice + ERP matches), stores an
`ApprovalSnapshot` (parameters, facts, sha256 hash, policy version), records `APPROVAL_REQUESTED`, sets
`WAITING_APPROVAL` and the thread ends. The console shows invoice, vendor, amount, due date and the requested
action with Approve / Reject.

**Decision**: `POST /api/approvals/{id}` → `APPROVAL_GRANTED` / `APPROVAL_REJECTED` (with note) → a new drive.

**Resume after approval**: the pending proposal is fed back through `_process`: validation → policy finds the
APPROVED record by fingerprint → `APPROVAL_REVALIDATION` re-fetches the facts → `policy.revalidate` checks status,
policy version, fingerprint, no ERP record appeared, facts hash unchanged → policy again → execute. On success the
approval becomes CONSUMED. If anything changed: `APPROVAL_INVALIDATED`, approval STALE, the model re-reads and a new
approval is requested (test: amount amended from ₹75,000 to ₹79,500 after approval → nothing written, fresh
approval for ₹79,500).

**Rejection**: final for that exact action (policy denies it afterwards); the agent finishes `not_entered`.

**Not an approval**: a free-text `ask_human` answer. Policy denies approval-seeking questions, because they have no
snapshot and no time-of-use check. (Found live: the model asked "Do you approve…?" itself before the gate did.)
