# brain/ — engineering source of truth

These documents describe the system as built. When code and a document disagree, the code is right and the
document is a bug.

| File | What it answers |
|---|---|
| [brain.md](brain.md) | Vision, core invariants, trust boundaries, what "autonomous" means here, scope, non-goals |
| [prd.md](prd.md) | User problem, user story, success criteria, demo scenarios |
| [trd.md](trd.md) | Frameworks, runtime, database, APIs, browser automation, LLM integration, deployment |
| [architecture.md](architecture.md) | Components and how a request flows through them |
| [data_model.md](data_model.md) | TaskState, GoalSpec, ActionProposal, Observation, ApprovalSnapshot, ExecutionEvent, ERP tables |
| [tool_contracts.md](tool_contracts.md) | Every tool: arguments, result, channel, errors |
| [runtime.md](runtime.md) | The engine loop and the single execution pipeline |
| [failure_recovery.md](failure_recovery.md) | Retry, adapt, replan, human, fail |
| [verification.md](verification.md) | The independent verifier |
| [security.md](security.md) | Prompt injection, allowlisting, approval, TOCTOU, secrets, data boundaries |
| [prompt_injection.md](prompt_injection.md) | Why document text can't steer the worker |
| [human_approval.md](human_approval.md) | Approval records, decisions, resume |
| [idempotency.md](idempotency.md) | Keys, constraints, conflicts |
| [crash_recovery.md](crash_recovery.md) | What survives a crash and how it is reconciled |
| [evaluation.md](evaluation.md) | Mapping to the evaluation criteria, with measured results |
| [testing.md](testing.md) | The test suite and how to run it |
| [demo.md](demo.md) | Demo script and video production |
| [deployment.md](deployment.md) | Vercel + Render setup |
| [implementation_plan.md](implementation_plan.md) | The phases, as executed |
| [decisions.md](decisions.md) | Decision log with alternatives |
| [limitations.md](limitations.md) | What is not production-ready |
