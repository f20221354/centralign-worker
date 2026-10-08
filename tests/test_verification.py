"""The verifier trusts the source systems, not the agent."""
from src.contracts import GoalSpec, RunStatus, TaskState
from src.runtime.engine import Engine
from src.verification.verifier import verify
from tests.conftest import Planner, drive_to_end


def base_state(erp, **kw) -> TaskState:
    fields = {"vendor_id": "V-ACME", "vendor_name": "Acme Corp", "selected_invoice_id": "INV-1042", **kw}
    return TaskState(run_id="v1", goal_text="g", status=RunStatus.PENDING_VERIFICATION,
                     goal_spec=GoalSpec(objective="o", vendor_name="Acme Corp"), erp_baseline_ids=erp.all_ids(),
                     **fields)


def failed(result):
    return {c["name"] for c in result["checks"] if not c["passed"]}


def test_agent_claim_without_erp_record_fails(apis):
    portal, erp = apis
    s = base_state(erp, finish_outcome="entered")
    r = verify(s, [], portal, erp)
    assert not r["passed"] and "exactly one ERP record for the invoice" in failed(r)


def test_wrong_amount_in_erp_fails(apis):
    portal, erp = apis
    s = base_state(erp, finish_outcome="entered")
    erp.create_via_api({"vendor_name": "Acme Corp", "invoice_number": "INV-1042", "invoice_date": "2026-10-05",
                        "due_date": "2026-10-25", "amount_inr": "46205.50"})  # transposed digits
    r = verify(s, [], portal, erp)
    assert "amount matches (paise)" in failed(r)


def test_wrong_invoice_processed_fails(apis):
    portal, erp = apis
    s = base_state(erp, finish_outcome="entered")
    s.selected_invoice_id = "INV-1045"  # highest number, not the latest date
    r = verify(s, [], portal, erp)
    assert "worker processed the latest invoice" in failed(r)


def test_unexpected_extra_write_fails(apis):
    portal, erp = apis
    s = base_state(erp, finish_outcome="entered")
    erp.create_via_api({"vendor_name": "Acme Corp", "invoice_number": "INV-1042", "invoice_date": "2026-10-05",
                        "due_date": "2026-10-25", "amount_inr": "46250.50"})
    erp.create_via_api({"vendor_name": "Acme Corp", "invoice_number": "INV-1045", "invoice_date": "2026-08-28",
                        "due_date": "2026-09-27", "amount_inr": "61200.00"})
    r = verify(s, [], portal, erp)
    assert failed(r) == {"no unexpected ERP writes"}


def test_above_threshold_write_without_approval_fails(apis):
    portal, erp = apis
    s = base_state(erp, finish_outcome="entered", vendor_id="V-GAMMA", vendor_name="Gamma Industries",
                   selected_invoice_id="GI-7781")
    s.goal_spec = GoalSpec(objective="o", vendor_name="Gamma Industries")
    erp.create_via_api({"vendor_name": "Gamma Industries", "invoice_number": "GI-7781", "invoice_date": "2026-10-03",
                        "due_date": "2026-11-02", "amount_inr": "75000.00"})
    assert "human approval on record" in failed(verify(s, [], portal, erp))


def test_misreported_outcome_fails(apis):
    portal, erp = apis
    s = base_state(erp, finish_outcome="already_present")
    erp.create_via_api({"vendor_name": "Acme Corp", "invoice_number": "INV-1042", "invoice_date": "2026-10-05",
                        "due_date": "2026-10-25", "amount_inr": "46250.50"})
    assert "worker's claim matches the ERP" in failed(verify(s, [], portal, erp))


def test_failed_verification_gets_one_replan_round_through_the_pipeline(apis, store):
    """The agent finishes too early; the verifier sends it back once; it fixes the work."""
    early = {"tool_name": "finish", "arguments": {"outcome": "entered", "summary": "done (not really)"},
             "action_rationale": "x"}
    eng = Engine(store, Planner("Acme Corp", overrides=[early]))
    run = eng.start("Process the latest Acme Corp invoice")
    s = drive_to_end(eng, run)
    ev = [e["event_type"] for e in store.events(run)]
    assert ev.count("VERIFICATION_COMPLETED") == 2
    assert s.status == RunStatus.COMPLETED
