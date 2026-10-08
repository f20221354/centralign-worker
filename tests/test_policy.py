"""Policy gate: deterministic decisions from validated actions, observed state and approval records."""
from datetime import date

from src.contracts import (
    ApprovalSnapshot,
    AskHumanArgs,
    CreateInternalInvoiceArgs,
    GoalSpec,
    SearchVendorArgs,
    TaskState,
    UIHints,
)
from src.runtime import policy

CANDS = [{"invoice_number": "INV-1045", "invoice_date": "2026-08-28"},
         {"invoice_number": "INV-1042", "invoice_date": "2026-10-05"}]


def state(amount="46250.50", mode="enter_if_missing", threshold=None, **kw) -> TaskState:
    paise = int(round(float(amount) * 100))
    return TaskState(run_id="r1", goal_text="g", status="RUNNING",
                     goal_spec=GoalSpec(objective="o", vendor_name="Acme Corp", mode=mode,
                                        approval_threshold_paise=threshold),
                     vendor_id="V-ACME", vendor_name="Acme Corp", candidate_invoices=CANDS,
                     selected_invoice_id="INV-1042",
                     source_invoice_data={"invoice_number": "INV-1042", "invoice_date": "2026-10-05",
                                          "due_date": "2026-10-25", "amount_paise": paise, "po_number": "PO-5521"},
                     **kw)


def args(amount="46250.50", number="INV-1042", **kw) -> CreateInternalInvoiceArgs:
    return CreateInternalInvoiceArgs(vendor_name="Acme Corp", invoice_number=number, invoice_date=date(2026, 10, 5),
                                     due_date=date(2026, 10, 25), amount_inr=amount, po_number="PO-5521", **kw)


def approval(a: CreateInternalInvoiceArgs, status="APPROVED") -> ApprovalSnapshot:
    return ApprovalSnapshot(approval_id="ap1", run_id="r1", action_id="x", tool_name="create_internal_invoice",
                            parameters=a.business_fields(), critical_state_facts={}, policy_version=policy.POLICY_VERSION,
                            state_payload_hash="h", requested_at="t", status=status)


def test_read_tools_allowed():
    assert policy.evaluate("search_vendor", SearchVendorArgs(query="Acme"), state(), []).kind == "ALLOW"


def test_small_invoice_allowed():
    assert policy.evaluate("create_internal_invoice", args(), state(), []).kind == "ALLOW"


def test_above_threshold_requires_approval():
    d = policy.evaluate("create_internal_invoice", args("75000.00"), state("75000.00"), [])
    assert d.kind == "REQUIRE_APPROVAL" and "₹50,000.00" in d.reason


def test_exactly_threshold_does_not_require_approval():
    assert policy.evaluate("create_internal_invoice", args("50000.00"), state("50000.00"), []).kind == "ALLOW"


def test_user_can_tighten_but_not_loosen_threshold():
    assert policy.evaluate("create_internal_invoice", args(), state(threshold=20_000_00), []).kind == "REQUIRE_APPROVAL"
    loose = state("75000.00", threshold=1_000_000_00)
    assert policy.evaluate("create_internal_invoice", args("75000.00"), loose, []).kind == "REQUIRE_APPROVAL"


def test_approval_record_allows_and_ui_hints_do_not_change_fingerprint():
    a = args("75000.00")
    s = state("75000.00")
    adapted = args("75000.00", ui_hints=UIHints(submit_button_label="Add Invoice"))
    d = policy.evaluate("create_internal_invoice", adapted, s, [approval(a)])
    assert d.kind == "ALLOW" and d.approval_id == "ap1"


def test_approval_for_other_amount_does_not_count():
    s = state("75000.00")
    other = approval(args("74000.00"))
    assert policy.evaluate("create_internal_invoice", args("75000.00"), s, [other]).kind == "REQUIRE_APPROVAL"


def test_rejected_action_is_denied():
    a = args("75000.00")
    assert policy.evaluate("create_internal_invoice", a, state("75000.00"), [approval(a, "REJECTED")]).kind == "DENY"


def test_not_latest_is_denied():
    s = state()
    s.selected_invoice_id = "INV-1045"
    s.source_invoice_data = {**s.source_invoice_data, "invoice_number": "INV-1045"}
    d = policy.evaluate("create_internal_invoice", args(number="INV-1045"), s, [])
    assert d.kind == "DENY" and "latest" in d.reason


def test_must_extract_first():
    s = state()
    s.source_invoice_data = None
    assert policy.evaluate("create_internal_invoice", args(), s, []).kind == "DENY"


def test_arguments_must_match_source_data():
    d = policy.evaluate("create_internal_invoice", args("46250.00"), state(), [])
    assert d.kind == "DENY" and "amount_paise" in d.reason


def test_duplicate_denied_when_already_in_erp():
    s = state()
    s.erp_record = {"id": 9}
    assert policy.evaluate("create_internal_invoice", args(), s, []).kind == "DENY"


def test_report_only_goal_cannot_write():
    assert policy.evaluate("create_internal_invoice", args(), state(mode="report_only"), []).kind == "DENY"


def test_wrong_vendor_denied():
    s = state()
    s.vendor_name = "Acme Corporate Travel"
    assert policy.evaluate("create_internal_invoice", args(), s, []).kind == "DENY"


def test_ask_human_cannot_be_used_as_approval():
    d = policy.evaluate("ask_human", AskHumanArgs(question="Do you approve entering GI-7781?"), state(), [])
    assert d.kind == "DENY"
    assert policy.evaluate("ask_human", AskHumanArgs(question="Which Acme do you mean?"), state(), []).kind == "ALLOW"


def test_unknown_tool_denied():
    assert policy.evaluate("transfer_payment", SearchVendorArgs(query="xx"), state(), []).kind == "DENY"


def test_revalidate_detects_changed_facts_and_new_erp_record():
    a = args("75000.00")
    s = state("75000.00")
    facts = {"invoice_number": "INV-1042", "amount_paise": 7500000, "erp_existing_records": 0}
    snap = approval(a)
    snap.critical_state_facts, snap.state_payload_hash = facts, policy.stable_hash(facts)
    assert policy.revalidate(snap, facts, a, s, [snap]).kind == "ALLOW"
    changed = {**facts, "amount_paise": 7600000}
    assert "amount_paise" in policy.revalidate(snap, changed, a, s, [snap]).reason
    appeared = {**facts, "erp_existing_records": 1}
    assert policy.revalidate(snap, appeared, a, s, [snap]).kind == "DENY"
    old = snap.model_copy(update={"policy_version": "old"})
    assert "policy changed" in policy.revalidate(old, facts, a, s, [old]).reason
