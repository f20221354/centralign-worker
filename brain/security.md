# Security

| Threat | Control | Where |
|---|---|---|
| Model calls something dangerous | Allowlist of 8 tools; `ActionProposal.tool_name` is a Literal; no code-exec or DB tool exists | contracts.py, policy.py |
| Model passes extra or malformed arguments (`human_approved=true`, SQL in ids) | `extra="forbid"`, patterns, typed dates, Decimal amounts; rejected before policy | contracts.py |
| Prompt injection in documents | text fenced as data; extraction output validated; policy never reads document text; detection logged | prompt_injection.md |
| Self-approval | approvals are server-side rows decided only via the console API; `ask_human` cannot be used as approval (policy DENY) | engine.decide, policy.evaluate |
| Approval reused for something else | fingerprint of the exact business fields; any change requires a new approval; rejected actions stay denied | policy.fingerprint |
| Time-of-check / time-of-use | live re-fetch of portal + ERP facts, hash compare, policy-version check, conflicting-record check, then policy again | policy.revalidate |
| Wrong invoice / fabricated values | create allowed only for the selected, extracted, latest-by-date invoice with values equal to the extracted source | policy.evaluate |
| Duplicates | DB unique constraints + idempotency keys + reconciliation | idempotency.md |
| Money rounding | integer paise only | contracts.to_paise |
| Secrets | API keys only in env vars (`.env` gitignored, Render secrets); ERP credentials used by the executor, never sent to the model; `/healthz` lists provider names only | browser_executor, client |
| Audit tampering | events table append-only via SQLite triggers; no chain-of-thought stored | trace.py |
| Abuse of the public demo | one run at a time, `RUNS_PER_HOUR` limit, synthetic data only, mock systems only | main.py |
| Path traversal on evidence files | run id and file name restricted to `[\w.-]+` | main._file |

Data boundaries: everything is synthetic; no real vendor, ERP or personal data; the deployed demo talks only to
its own mock apps and the LLM provider.
