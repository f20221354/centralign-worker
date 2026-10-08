"""The policy gate: plain, deterministic code between the model's proposals and the executors.

It never calls a model and never reads document text as instructions. Its inputs are the validated action,
the runtime state (facts observed by tools) and server-side approval records.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from src.contracts import ApprovalSnapshot, CreateInternalInvoiceArgs, TaskState, fmt_inr, stable_hash, to_paise
from src.runtime.state import latest_by_date

POLICY_VERSION = "ap-policy-2026.10.1"
COMPANY_APPROVAL_THRESHOLD_PAISE = 50_000_00  # ₹50,000
ALLOWED_TOOLS = {"search_vendor", "list_invoices", "open_invoice", "extract_invoice", "search_internal_invoices",
                 "create_internal_invoice", "ask_human", "finish"}
SIDE_EFFECT_TOOLS = {"create_internal_invoice"}


@dataclass
class Decision:
    kind: Literal["ALLOW", "DENY", "REQUIRE_APPROVAL"]
    reason: str = ""
    approval_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"decision": self.kind, "reason": self.reason, "approval_id": self.approval_id,
                "policy_version": POLICY_VERSION}


def vendor_key(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def threshold(state: TaskState) -> int:
    """The user can make approval stricter, never looser."""
    user = state.goal_spec.approval_threshold_paise if state.goal_spec else None
    return min(COMPANY_APPROVAL_THRESHOLD_PAISE, user or COMPANY_APPROVAL_THRESHOLD_PAISE)


def fingerprint(fields: dict[str, Any]) -> str:
    return stable_hash({"tool": "create_internal_invoice", **fields})


def source_diffs(args: CreateInternalInvoiceArgs, src: dict[str, Any]) -> list[str]:
    want = args.business_fields()
    have = {"invoice_number": src.get("invoice_number"), "invoice_date": src.get("invoice_date"),
            "due_date": src.get("due_date"), "amount_paise": src.get("amount_paise"),
            "po_number": src.get("po_number")}
    return [f"{k}: proposed {want[k]!r}, source says {have[k]!r}" for k in have if want[k] != have[k]]


def evaluate(tool: str, args: BaseModel, state: TaskState, approvals: list[ApprovalSnapshot]) -> Decision:
    if tool not in ALLOWED_TOOLS:
        return Decision("DENY", f"tool '{tool}' is not on the allowlist")
    if tool == "ask_human" and re.search(r"\bapprov", getattr(args, "question", ""), re.I):
        # A free-text answer is not an approval: no snapshot, no time-of-use check. Approvals go through the gate.
        return Decision("DENY", "approvals are not requested with ask_human; propose create_internal_invoice and "
                        "the policy gate requests a recorded approval if one is required")
    if tool != "create_internal_invoice":
        return Decision("ALLOW")
    assert isinstance(args, CreateInternalInvoiceArgs)
    goal = state.goal_spec
    if goal and goal.mode == "report_only":
        return Decision("DENY", "the goal only asks to check the ERP, not to enter anything")
    src = state.source_invoice_data
    if not src:
        return Decision("DENY", "extract the invoice first: ERP entries must come from extracted source data")
    if args.invoice_number != state.selected_invoice_id or args.invoice_number != src.get("invoice_number"):
        return Decision("DENY", f"{args.invoice_number} is not the invoice that was selected and extracted")
    latest = latest_by_date(state.candidate_invoices)
    if not latest or latest["invoice_number"] != args.invoice_number:
        return Decision("DENY", f"{args.invoice_number} is not the latest invoice by date"
                        + (f" (latest is {latest['invoice_number']})" if latest else ""))
    if vendor_key(args.vendor_name) != vendor_key(state.vendor_name):
        return Decision("DENY", f"vendor '{args.vendor_name}' differs from the portal vendor '{state.vendor_name}'")
    if goal and vendor_key(state.vendor_name) != vendor_key(goal.vendor_name):
        return Decision("DENY", f"portal vendor '{state.vendor_name}' is not the goal's vendor '{goal.vendor_name}'")
    if diffs := source_diffs(args, src):
        return Decision("DENY", "arguments differ from the extracted source data: " + "; ".join(diffs))
    if state.erp_record:
        return Decision("DENY", f"already in the ERP (record #{state.erp_record.get('id')}); creating it again "
                        "would duplicate it")
    fp = fingerprint(args.business_fields())
    if any(a.run_id == state.run_id and a.status == "REJECTED" and fingerprint(a.parameters) == fp
           for a in approvals):
        return Decision("DENY", "a human rejected this exact action")
    limit = threshold(state)
    if args.amount_paise > limit:
        for a in approvals:
            if a.run_id == state.run_id and a.status == "APPROVED" and fingerprint(a.parameters) == fp:
                return Decision("ALLOW", "human approval on record", a.approval_id)
        return Decision("REQUIRE_APPROVAL", f"{fmt_inr(args.amount_paise)} is above the approval threshold "
                        f"{fmt_inr(limit)}")
    return Decision("ALLOW")


def critical_facts(args: CreateInternalInvoiceArgs, portal_invoice: dict[str, Any] | None,
                   erp_matches: list[dict[str, Any]]) -> dict[str, Any]:
    """The facts an approval depends on, read live from the source systems."""
    p = portal_invoice or {}
    return {"invoice_number": args.invoice_number, "vendor_name": p.get("vendor_name"),
            "amount_paise": to_paise(p["amount_inr"]) if p.get("amount_inr") else None,
            "invoice_date": p.get("invoice_date"), "due_date": p.get("due_date"), "portal_status": p.get("status"),
            "erp_existing_records": len(erp_matches)}


def revalidate(snapshot: ApprovalSnapshot, live: dict[str, Any], args: CreateInternalInvoiceArgs,
               state: TaskState, approvals: list[ApprovalSnapshot]) -> Decision:
    """Time-of-use check after approval: same policy, same facts, no conflicting ERP record, then policy again."""
    if snapshot.status != "APPROVED":
        return Decision("DENY", f"approval is {snapshot.status}")
    if snapshot.policy_version != POLICY_VERSION:
        return Decision("DENY", f"policy changed since approval ({snapshot.policy_version} -> {POLICY_VERSION})")
    if fingerprint(snapshot.parameters) != fingerprint(args.business_fields()):
        return Decision("DENY", "the action differs from what was approved")
    if live["erp_existing_records"]:
        return Decision("DENY", "an ERP record for this invoice appeared after approval")
    if stable_hash(live) != snapshot.state_payload_hash:
        changed = [k for k in live if live[k] != snapshot.critical_state_facts.get(k)]
        return Decision("DENY", "facts changed since approval: " + ", ".join(changed))
    return evaluate("create_internal_invoice", args, state, approvals)
