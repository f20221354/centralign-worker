# CentrAlign — brain

## Product vision
A finance team member types one sentence ("Find the latest invoice from Acme Corp, extract the amount and due
date, enter it into our internal ERP, and tell me once it is done") and gets back finished, verified work with
evidence. The worker figures out the steps, uses the same screens a person would, recovers from the everyday
ways software fails, stops for a human when company policy says so, and never claims success it hasn't proven.

## Core invariants (enforced in code, covered by tests)
1. **One path to side effects.** Every action is: proposal → Pydantic validation → policy → executor →
   observation → classification → state update → event. Adapted actions, approved actions and actions after a
   crash all re-enter this path (`Engine._process`). There is no second path.
2. **The model proposes, it never decides.** It cannot execute code, touch a database, approve anything or mark
   a run complete. Unknown tools and unknown arguments (e.g. `human_approved`) are rejected at validation.
3. **Approval is server-side state.** An approval is an `ApprovalSnapshot` row with the exact business fields,
   the live facts it was based on, their hash and the policy version. Using it requires a fresh time-of-use
   check (`policy.revalidate`).
4. **"Done" is a claim.** The agent's `finish` only moves the run to `PENDING_VERIFICATION`. Only the
   independent verifier can make it `COMPLETED`.
5. **Documents are data.** PDF and page text is fenced as untrusted data in prompts and never reaches policy.
6. **Money is integer paise.** No float ever decides anything.
7. **One business invoice, one ERP record.** Database constraints plus idempotency keys; conflicting data is a
   409, never a silent "exists".
8. **Execution memory is the TaskState, not the chat.** Each decision is made from the persisted state and recent
   observations, so a restart loses nothing that matters.
9. **No chain-of-thought is stored or shown.** Only a one-sentence action rationale and observable facts.

## Trust boundaries
| Component | Trusted for | Not trusted for |
|---|---|---|
| LLM | choosing the next action, reading a document | permissions, money checks, approvals, completion |
| Runtime + policy | enforcing rules, sequencing, recovery | knowing what the user meant (that's the GoalSpec) |
| Executors | performing an action and reporting what they saw | deciding whether that was success for the goal |
| Observations | facts about the world | instructions |
| Verifier | deciding whether the goal was achieved | — it reads source systems, not the agent |
| Human | approvals, clarifications, restarts | — |

## What "autonomous" means here
The user states an outcome. The worker decides: which vendor (Acme Corp, not Acme Corporate Travel), which
invoice is latest (by date: INV-1042, although INV-1045 has a higher number), what to extract, whether the ERP
already has it, how to interact with each system, what to do after each failure, and when it is finished. The
human is consulted only where policy or genuine ambiguity requires it.

## Scope
Accounts payable intake for one company over two systems (a vendor portal and an internal ERP), with real
browser execution, real persistence and real failure modes.

## Non-goals
A general computer-use agent, multi-agent orchestration, arbitrary websites, payments, desktop automation,
vector databases, production-scale orchestration.
