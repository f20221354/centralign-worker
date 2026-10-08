"""Independent verification. Trusts the source systems, not the agent.

It re-derives the target from the portal's API (vendor by name, latest invoice by date), reads the ERP through
its API (a different channel from the browser the worker wrote through) and compares field by field in paise.
The agent's final message is only checked for consistency with what the ERP shows.
"""
from __future__ import annotations

from typing import Any

from src.contracts import ApprovalSnapshot, TaskState, fmt_inr, to_paise
from src.execution.api_executor import ERPAPI, PortalAPI, erp_url
from src.runtime.policy import threshold, vendor_key
from src.runtime.state import latest_by_date


def verify(state: TaskState, approvals: list[ApprovalSnapshot], portal: PortalAPI, erp: ERPAPI) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str = "") -> bool:
        checks.append({"name": name, "passed": bool(ok), "detail": detail})
        return bool(ok)

    goal = state.goal_spec
    if not goal:
        check("goal understood", False)
        return {"passed": False, "checks": checks, "evidence": {}}

    vendors = [v for v in portal.vendors(goal.vendor_name) if vendor_key(v["name"]) == vendor_key(goal.vendor_name)]
    if not check("vendor exists on the portal", len(vendors) == 1, goal.vendor_name):
        return {"passed": False, "checks": checks, "evidence": {}}
    vendor = vendors[0]
    source = latest_by_date(portal.invoices(vendor["vendor_id"]))
    if not check("latest invoice is unambiguous", source is not None):
        return {"passed": False, "checks": checks, "evidence": {}}
    assert source is not None
    src_paise = to_paise(source["amount_inr"])
    check("worker processed the latest invoice", state.selected_invoice_id == source["invoice_number"],
          f"latest by date is {source['invoice_number']} ({source['invoice_date']}); worker chose "
          f"{state.selected_invoice_id}")

    rows = erp.search(vendor_name=vendor["name"], invoice_number=source["invoice_number"])
    new_ids = sorted(set(erp.all_ids()) - set(state.erp_baseline_ids))
    rec = rows[0] if len(rows) == 1 else None
    created_now = bool(rec and rec["id"] in new_ids)

    if goal.mode == "report_only":
        check("no ERP writes", not new_ids, f"new records: {new_ids}" if new_ids else "")
        claim = state.finish_outcome
        present = len(rows) > 0
        check("worker's answer matches the ERP", (claim == "already_present") == present,
              f"ERP has {len(rows)} record(s); worker said {claim}")
    else:
        if check("exactly one ERP record for the invoice", len(rows) == 1, f"found {len(rows)}") and rec:
            check("vendor matches", vendor_key(rec["vendor_name"]) == vendor_key(vendor["name"]), rec["vendor_name"])
            check("invoice number matches", rec["invoice_number"] == source["invoice_number"], rec["invoice_number"])
            check("amount matches (paise)", rec["amount_paise"] == src_paise,
                  f"source {fmt_inr(src_paise)} / ERP {fmt_inr(rec['amount_paise'])}")
            check("due date matches", rec["due_date"] == source["due_date"],
                  f"source {source['due_date']} / ERP {rec['due_date']}")
            check("invoice date matches", rec["invoice_date"] == source["invoice_date"],
                  f"source {source['invoice_date']} / ERP {rec['invoice_date']}")
        unexpected = [i for i in new_ids if not rec or i != rec["id"]]
        check("no unexpected ERP writes", not unexpected, f"unexpected new records: {unexpected}" if unexpected else "")
        if created_now and src_paise > threshold(state):
            fields = {"vendor_name": vendor_key(vendor["name"]), "invoice_number": source["invoice_number"],
                      "invoice_date": source["invoice_date"], "due_date": source["due_date"],
                      "amount_paise": src_paise}
            ok = any(a.status in ("APPROVED", "CONSUMED") and
                     {k: a.parameters.get(k) for k in ("invoice_number", "amount_paise", "due_date")} ==
                     {k: fields[k] for k in ("invoice_number", "amount_paise", "due_date")} for a in approvals)
            check("human approval on record", ok, f"amount {fmt_inr(src_paise)} > {fmt_inr(threshold(state))}")
        claim = state.finish_outcome
        expected_claim = "entered" if created_now else "already_present"
        check("worker's claim matches the ERP", rec is not None and claim == expected_claim,
              f"worker said '{claim}'; ERP shows {'created in this run' if created_now else 'pre-existing'}")

    attempts = sum(s.attempts for s in state.executed_steps)
    evidence = {
        "vendor": vendor["name"], "invoice": source["invoice_number"], "invoice_date": source["invoice_date"],
        "amount": fmt_inr(src_paise), "due_date": source["due_date"],
        "erp_record": (f"#{rec['id']} " + ("created in this run" if created_now else "already present")) if rec
        else "none", "erp_url": f"{erp_url()}/invoices/{rec['id']}" if rec else None,
        "verification": "passed" if all(c["passed"] for c in checks) else "failed",
        "tool_attempts": attempts, "approval": next((a.status for a in reversed(approvals)), "not required"),
    }
    return {"passed": all(c["passed"] for c in checks), "checks": checks, "evidence": evidence}
