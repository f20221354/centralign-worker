"""ERP idempotency: the database, not the client, guarantees one record per business invoice."""
from concurrent.futures import ThreadPoolExecutor

from src.contracts import idempotency_key

BODY = {"vendor_name": "Acme Corp", "invoice_number": "INV-1042", "invoice_date": "2026-10-05",
        "due_date": "2026-10-25", "amount_inr": "46250.50", "po_number": "PO-5521"}


def test_identical_resubmit_is_idempotent_success(apis):
    _, erp = apis
    key = idempotency_key("Acme Corp", "INV-1042")
    r1 = erp.create_via_api({**BODY, "idempotency_key": key})
    r2 = erp.create_via_api({**BODY, "idempotency_key": key})
    assert (r1.status_code, r1.json()["outcome"]) == (201, "created")
    assert (r2.status_code, r2.json()["outcome"]) == (200, "exists")
    assert len(erp.search(vendor_name="Acme Corp", invoice_number="INV-1042")) == 1


def test_conflicting_financial_data_is_a_409_not_exists(apis):
    _, erp = apis
    erp.create_via_api(BODY)
    r = erp.create_via_api({**BODY, "amount_inr": "75000.00"})
    assert r.status_code == 409 and r.json()["outcome"] == "conflict"
    assert "amount_paise" in r.json()["error"]


def test_vendor_name_case_does_not_create_duplicates(apis):
    _, erp = apis
    erp.create_via_api(BODY)
    assert erp.create_via_api({**BODY, "vendor_name": "ACME  corp"}).json()["outcome"] == "exists"


def test_reused_key_for_a_different_invoice_conflicts(apis):
    _, erp = apis
    erp.create_via_api({**BODY, "idempotency_key": "k1"})
    r = erp.create_via_api({**BODY, "invoice_number": "INV-9999", "idempotency_key": "k1"})
    assert r.status_code == 409


def test_concurrent_identical_submits_create_one_record(apis):
    _, erp = apis
    with ThreadPoolExecutor(8) as pool:
        codes = list(pool.map(lambda _: erp.create_via_api({**BODY, "idempotency_key": "kk"}).status_code, range(8)))
    assert codes.count(201) == 1 and set(codes) <= {200, 201}
    assert len(erp.search(vendor_name="Acme Corp", invoice_number="INV-1042")) == 1


def test_validation_errors_are_422(apis):
    _, erp = apis
    assert erp.create_via_api({**BODY, "due_date": "2026-01-01"}).status_code == 422
    assert erp.create_via_api({**BODY, "amount_inr": "abc"}).status_code == 422
