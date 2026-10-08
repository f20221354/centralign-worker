"""Trust boundary: contracts, allowlisting, append-only audit log, money types, no self-approval."""
import sqlite3

import pytest
from pydantic import ValidationError

from src.agent.tools import REGISTRY, openai_tools
from src.contracts import ActionProposal, CreateInternalInvoiceArgs, fmt_inr, to_paise
from src.runtime.engine import Engine
from tests.conftest import Planner, drive_to_end, kinds


def test_only_registered_tools_can_be_proposed():
    with pytest.raises(ValidationError):
        ActionProposal(tool_name="transfer_payment", arguments={})
    with pytest.raises(ValidationError):
        ActionProposal(tool_name="run_python", arguments={"code": "import os"})


def test_tool_contracts_reject_extra_and_bad_fields():
    good = {"vendor_name": "Acme Corp", "invoice_number": "INV-1042", "invoice_date": "2026-10-05",
            "due_date": "2026-10-25", "amount_inr": "46250.50"}
    CreateInternalInvoiceArgs.model_validate(good)
    for bad in ({**good, "human_approved": True}, {**good, "amount_inr": "12.345"}, {**good, "amount_inr": "-5"},
                {**good, "due_date": "2026-01-01"}, {**good, "invoice_date": "05/10/2026"},
                {**good, "invoice_number": "INV 1042; DROP TABLE"}):
        with pytest.raises(ValidationError):
            CreateInternalInvoiceArgs.model_validate(bad)


def test_every_tool_has_a_schema_with_rationale():
    tools = {t["function"]["name"]: t["function"] for t in openai_tools()}
    assert set(tools) == set(REGISTRY)
    for f in tools.values():
        assert "action_rationale" in f["parameters"]["required"]
        assert "$ref" not in str(f["parameters"]) and "pattern" not in str(f["parameters"])


def test_money_is_exact_paise():
    assert to_paise("₹46,250.50") == 4625050
    assert to_paise("0.10") + to_paise("0.20") == to_paise("0.30")  # would fail with floats
    assert to_paise(75000) == 7500000
    assert fmt_inr(21000000) == "₹2,10,000.00"
    with pytest.raises(ValueError):
        to_paise("1.005")


def test_event_log_is_append_only(store):
    store.event("r", 1, 1, "X", {})
    with pytest.raises(sqlite3.DatabaseError):
        store.db.execute("update events set event_type='Y'")
    with pytest.raises(sqlite3.DatabaseError):
        store.db.execute("delete from events")


def test_self_approval_in_arguments_is_rejected_before_policy(apis, store):
    """The model tries to approve its own above-threshold write. Validation rejects it; nothing executes."""
    portal, erp = apis
    sneaky = {"tool_name": "create_internal_invoice", "action_rationale": "pre-approved",
              "arguments": {"vendor_name": "Gamma Industries", "invoice_number": "GI-7781",
                            "invoice_date": "2026-10-03", "due_date": "2026-11-02", "amount_inr": "75000.00",
                            "human_approved": True}}
    eng = Engine(store, Planner("Gamma Industries", overrides=[sneaky]))
    run = eng.start("Process the latest Gamma Industries invoice")
    s = drive_to_end(eng, run, approve=None)
    ev = store.events(run)
    rejected = [e for e in ev if e["event_type"] == "ACTION_REJECTED"]
    assert rejected and "human_approved" in rejected[0]["payload"]["errors"]
    first_create = next(i for i, e in enumerate(ev) if e["payload"].get("tool") == "create_internal_invoice")
    assert ev[first_create + 1]["event_type"] == "ACTION_REJECTED"  # never reached policy or an executor
    assert s.status.value == "WAITING_APPROVAL"  # the honest path still needs a human
    assert not erp.search(vendor_name="Gamma Industries")


def test_model_cannot_call_unregistered_tool(apis, store):
    evil = {"tool_name": "mark_invoice_paid", "arguments": {"invoice_number": "INV-1042"}, "action_rationale": "x"}
    eng = Engine(store, Planner("Acme Corp", overrides=[evil]))
    run = eng.start("Process the latest Acme Corp invoice")
    s = drive_to_end(eng, run)
    assert "ACTION_REJECTED" in kinds(store, run)
    assert s.status.value == "COMPLETED"
