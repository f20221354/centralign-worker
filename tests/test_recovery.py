"""Failure handling end to end: real browser, real ERP, injected failures."""
from src.contracts import Observation, ObsStatus, Outcome, RunStatus, idempotency_key
from src.runtime.classifier import classify
from src.runtime.engine import Engine
from tests.conftest import Planner, drive_to_end, kinds


def obs(status, http=None, **data):
    return Observation(action_name="x", status=status, http_status=http, data=data)


def test_classifier_routes():
    assert classify(obs(ObsStatus.SUCCESS), 1, 0)[0] == Outcome.CONTINUE
    assert classify(obs(ObsStatus.HTTP_ERROR, 500), 1, 0)[0] == Outcome.RETRY
    assert classify(obs(ObsStatus.HTTP_ERROR, 500), 3, 0)[0] == Outcome.FAIL
    assert classify(obs(ObsStatus.HTTP_ERROR, 404), 1, 0)[0] == Outcome.REPLAN
    assert classify(obs(ObsStatus.DOM_ERROR), 1, 0)[0] == Outcome.ADAPT
    assert classify(obs(ObsStatus.DOM_ERROR), 1, 2)[0] == Outcome.FAIL
    assert classify(obs(ObsStatus.VALIDATION_ERROR, 422), 1, 0)[0] == Outcome.REPLAN  # never retried blindly
    assert classify(obs(ObsStatus.CONFLICT, 409), 1, 0)[0] == Outcome.HUMAN
    assert classify(obs(ObsStatus.DATA_AMBIGUITY), 1, 0)[0] == Outcome.HUMAN
    assert classify(obs(ObsStatus.VALIDATION_ERROR, fatal=True), 1, 0)[0] == Outcome.FAIL


def test_happy_path(apis, store):
    _, erp = apis
    eng = Engine(store, Planner("Acme Corp"))
    run = eng.start("Find the latest invoice from Acme Corp and enter it into the ERP")
    s = drive_to_end(eng, run)
    assert s.status == RunStatus.COMPLETED and s.verification["passed"]
    rows = erp.search(vendor_name="Acme Corp", invoice_number="INV-1042")
    assert len(rows) == 1 and rows[0]["amount_paise"] == 4625050 and rows[0]["due_date"] == "2026-10-25"
    assert rows[0]["created_by"] == "ops (web)"  # written through the browser form
    assert s.erp_record and s.erp_record["id"] == rows[0]["id"]  # the observation was read back correctly


def test_transient_500_is_retried_and_each_attempt_logged(apis, store):
    _, erp = apis
    erp.admin("chaos", {"fail_next_create": 1})
    eng = Engine(store, Planner("Acme Corp"))
    run = eng.start("Process the latest Acme Corp invoice")
    s = drive_to_end(eng, run)
    ev = store.events(run)
    starts = [e for e in ev if e["event_type"] == "ACTION_STARTED" and e["payload"]["tool"] == "create_internal_invoice"]
    assert [e["attempt_index"] for e in starts] == [1, 2]
    assert any(e["event_type"] == "RECOVERY_ATTEMPTED" and e["payload"]["strategy"] == "retry" for e in ev)
    assert s.status == RunStatus.COMPLETED
    assert len(erp.search(vendor_name="Acme Corp", invoice_number="INV-1042")) == 1


def test_ui_change_is_adapted_and_adapted_action_is_policy_checked_again(apis, store):
    _, erp = apis
    erp.admin("chaos", {"rename_submit": 1})
    planner = Planner("Acme Corp")
    eng = Engine(store, planner)
    run = eng.start("Process the latest Acme Corp invoice")
    s = drive_to_end(eng, run)
    ev = store.events(run)
    assert any(e["event_type"] == "RECOVERY_ATTEMPTED" and e["payload"]["strategy"] == "adapt" for e in ev)
    creates = [e for e in ev if e["event_type"] == "POLICY_EVALUATED" and e["payload"]["tool"] == "create_internal_invoice"]
    assert len(creates) == 2  # original and adapted proposal both went through the gate
    adapted = [p for p in planner.proposals if p["arguments"].get("ui_hints")]
    assert adapted[0]["arguments"]["ui_hints"]["submit_button_label"] == "Add Invoice"
    assert s.status == RunStatus.COMPLETED


def test_crash_after_write_is_reconciled_without_duplicate(apis, store):
    _, erp = apis
    eng = Engine(store, Planner("Acme Corp"))
    run = eng.start("Process the latest Acme Corp invoice", {"crash_after_submit": True})
    s = drive_to_end(eng, run)
    k = kinds(store, run)
    assert "WORKER_CRASHED" in k and "WORKER_RESUMED" in k and "ACTION_RECONCILED" in k
    assert len(erp.search(vendor_name="Acme Corp", invoice_number="INV-1042")) == 1
    creates = [e for e in store.events(run) if e["event_type"] == "ACTION_STARTED" and e["payload"]["side_effect"]]
    assert len(creates) == 1  # the write was never repeated
    assert s.status == RunStatus.COMPLETED and s.finish_outcome == "entered"


def test_unrecorded_action_that_never_reached_erp_is_safe_to_redo(apis, store):
    _, erp = apis
    eng = Engine(store, Planner("Acme Corp"))
    run = eng.start("Process the latest Acme Corp invoice")
    s = store.load(run)
    s.status = RunStatus.INTERRUPTED
    store.save(s)
    store.event(run, 0, 1, "ACTION_STARTED", {"action_id": "ghost", "tool": "create_internal_invoice",
                                              "side_effect": True,
                                              "idempotency_key": idempotency_key("Acme Corp", "INV-1042")})
    s = drive_to_end(eng, run)
    assert "SIDE_EFFECT_NOT_FOUND" in kinds(store, run)
    assert s.status == RunStatus.COMPLETED
    assert len(erp.search(vendor_name="Acme Corp", invoice_number="INV-1042")) == 1
