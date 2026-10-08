# Failure recovery (src/runtime/classifier.py)

| Observation | Route | Behaviour | Demo / test |
|---|---|---|---|
| SUCCESS | CONTINUE | next decision | all |
| HTTP_ERROR 5xx, timeout, network | **RETRY** | same action, same idempotency key, backoff 0.8 s × 2ⁿ, ≤ 3 attempts, each attempt its own ACTION_STARTED / OBSERVATION_RECORDED; exhausted → FAIL | scenario 2, `test_transient_500_is_retried_and_each_attempt_logged` |
| HTTP_ERROR 4xx | REPLAN | not transient; the model chooses differently | `test_classifier_routes` |
| DOM_ERROR | **ADAPT** | the executor reports what is on the page (`available_buttons`); the model proposes the same tool with `ui_hints`; that proposal is validated and policy-checked again; ≤ 2 per run | scenario 3, `test_ui_change_is_adapted_and_adapted_action_is_policy_checked_again` |
| VALIDATION_ERROR, NOT_FOUND, POLICY_BLOCKED | **REPLAN** | never retried blindly; the reason goes to the model as a runtime note | `test_policy.py` |
| Selected invoice already in the ERP | **REPLAN** | `REPLANNED`: no write needed, finish `already_present` | scenario 7 |
| DATA_AMBIGUITY (PDF ≠ portal), CONFLICT (409) | **HUMAN** | the run asks a question and waits | `test_conflicting_financial_data_is_a_409_not_exists` |
| fatal configuration (ERP login rejected) | **FAIL** | recorded, no retries | `test_classifier_routes` |
| Verification failed | REPLAN once | the failed checks become a runtime note; second failure → FAILED | `test_failed_verification_gets_one_replan_round_through_the_pipeline` |
| Process died | reconcile | see crash_recovery.md | scenario 6 |
| LLM provider 429/5xx | provider failover | per-minute limits waited out; daily quota → 1 h cooldown; next provider | observed live: Groq 120b daily quota → gpt-oss-20b |

Business-rule failures (422, 409, policy) are never classified as transient.
