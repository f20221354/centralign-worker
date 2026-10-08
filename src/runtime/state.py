"""Task state updates. Observations are facts; this is the only place they change the execution memory."""
from __future__ import annotations

from typing import Any

from src.contracts import ApprovalSnapshot, Observation, ObsStatus, TaskState, norm

ERP_FIELDS = {"ERP id": "id", "Vendor": "vendor_name", "Invoice number": "invoice_number",
              "Invoice date": "invoice_date", "Due date": "due_date", "Amount (INR)": "amount_inr",
              "PO number": "po_number"}


def latest_by_date(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Deterministic: the newest invoice_date wins. A tie is ambiguous (None) and needs a human."""
    dated = [c for c in candidates if c.get("invoice_date")]
    if not dated:
        return None
    top = max(c["invoice_date"] for c in dated)
    best = [c for c in dated if c["invoice_date"] == top]
    return best[0] if len(best) == 1 else None


def _select(state: TaskState, number: str, data: dict[str, Any], notes: list[str]) -> None:
    if state.selected_invoice_id == number:
        return
    state.selected_invoice_id = number
    latest = latest_by_date(state.candidate_invoices)
    notes.append("INVOICE_SELECTED")
    data.setdefault("_selection", {"invoice_number": number,
                                   "is_latest_by_date": bool(latest and latest["invoice_number"] == number),
                                   "latest_by_date": latest and latest["invoice_number"]})


def apply(state: TaskState, tool: str, args: dict[str, Any], obs: Observation) -> list[str]:
    """Update state from a successful observation. Returns notable facts for the event log."""
    notes: list[str] = []
    if obs.status != ObsStatus.SUCCESS:
        return notes
    d = obs.data
    if tool == "list_invoices":
        state.vendor_id, state.vendor_name = d["vendor_id"], d["vendor_name"]
        state.candidate_invoices = d["invoices"]
    elif tool in ("open_invoice", "extract_invoice"):
        _select(state, args["invoice_number"], d, notes)
        if tool == "extract_invoice":
            state.source_invoice_data = d["invoice"]
            state.injection_warnings = d.get("injection_signals", [])
    elif tool == "search_internal_invoices":
        number = args.get("invoice_number")
        if number and not state.selected_invoice_id and any(c["invoice_number"] == number
                                                            for c in state.candidate_invoices):
            _select(state, number, d, notes)  # checking the ERP for one listed invoice is choosing it
        hit = next((m for m in d["matches"] if state.selected_invoice_id
                    and m["invoice_number"] == state.selected_invoice_id
                    and norm(m["vendor_name"]) == norm(state.vendor_name or "")), None)
        if hit:
            state.erp_record = hit
            notes.append("ALREADY_IN_ERP")
    elif tool == "create_internal_invoice":
        rec = {ERP_FIELDS.get(k, k): v for k, v in d.get("record", {}).items()}
        if rec.get("id"):
            rec["id"] = int(rec["id"])
        state.erp_record = rec
    return notes


def progress(state: TaskState, approvals: list[ApprovalSnapshot]) -> list[dict[str, str]]:
    """The checklist the console shows. Derived from state, so it can't claim more than the runtime knows."""
    latest = latest_by_date(state.candidate_invoices)
    ok_latest = bool(latest and state.selected_invoice_id == latest["invoice_number"])
    tools_ok = {s.tool_name for s in state.executed_steps if s.status == "SUCCESS"}
    items = [
        ("Goal understood", "done" if state.goal_spec else "todo"),
        ("Vendor identified", "done" if state.vendor_id else "todo"),
        ("Latest invoice found", "done" if ok_latest else ("warn" if state.selected_invoice_id else "todo")),
        ("Invoice data extracted", "done" if state.source_invoice_data else "todo"),
        ("ERP checked for this invoice", "done" if "search_internal_invoices" in tools_ok else "todo"),
    ]
    mine = [a for a in approvals if a.run_id == state.run_id]
    if mine:
        last = mine[-1]
        items.append(("Human approval", {"PENDING": "wait", "APPROVED": "done", "CONSUMED": "done",
                                         "REJECTED": "fail", "STALE": "warn"}[last.status]))
    if state.goal_spec and state.goal_spec.mode == "enter_if_missing":
        items.append(("Invoice recorded in ERP", "done" if state.erp_record else "todo"))
    v = state.verification
    items.append(("Independently verified", "todo" if not v else ("done" if v["passed"] else "fail")))
    return [{"label": a, "state": b} for a, b in items]
