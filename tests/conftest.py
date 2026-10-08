"""Test harness: real mock apps on a local port, real Chromium, a deterministic planner instead of the LLM.

The planner stands in for the model so tests are fast and repeatable; everything after the proposal (validation,
policy, executors, classifier, recovery, verifier) is the production code path.
"""
from __future__ import annotations

import os
import re
import socket
import tempfile
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

_tmp = Path(tempfile.mkdtemp(prefix="centralign-test-"))
os.environ["RUNS_DIR"] = str(_tmp / "runs")
os.environ["ERP_DATABASE_URL"] = f"sqlite:///{_tmp / 'erp.db'}"
os.environ.setdefault("BROWSER_TIMEOUT_MS", "8000")
os.environ.pop("RECORD_DIR", None)
_port = (lambda s: (s.bind(("127.0.0.1", 0)), s.getsockname()[1], s.close())[1])(socket.socket())
os.environ["PORTAL_URL"] = f"http://127.0.0.1:{_port}/portal"
os.environ["ERP_URL"] = f"http://127.0.0.1:{_port}/erp"

import pytest  # noqa: E402
import uvicorn  # noqa: E402

from src.contracts import GoalSpec, TaskState, to_paise  # noqa: E402
from src.execution.api_executor import ERPAPI, PortalAPI  # noqa: E402
from src.runtime.state import latest_by_date  # noqa: E402
from src.trace import Store  # noqa: E402

VENDOR_IDS = {"acme corp": "V-ACME", "beta manufacturing": "V-BETA", "gamma industries": "V-GAMMA",
              "delta supplies": "V-DELTA"}


@pytest.fixture(scope="session", autouse=True)
def server():
    from src.main import app
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=_port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    yield
    srv.should_exit = True


@pytest.fixture
def apis():
    portal, erp = PortalAPI(), ERPAPI()
    portal.admin("reset")
    erp.admin("reset")
    erp.admin("chaos", {"fail_next_create": 0, "rename_submit": 0})
    return portal, erp


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "worker.db")


class Planner:
    """Deterministic stand-in for the LLM. Reads the same state the model would and proposes the next action."""

    def __init__(self, vendor: str, mode: str = "enter_if_missing", threshold_inr: str | None = None,
                 overrides: list[dict] | None = None):
        self.spec = GoalSpec(objective=f"Process latest {vendor} invoice", vendor_name=vendor, mode=mode,
                             success_criteria=["latest invoice in ERP"],
                             approval_threshold_paise=to_paise(threshold_inr) if threshold_inr else None)
        self.overrides = list(overrides or [])  # scripted proposals injected first (adversarial tests)
        self.proposals: list[dict] = []

    def understand(self, goal_text: str) -> GoalSpec:
        return self.spec

    def extract(self, text: str) -> dict[str, Any]:
        def grab(label: str) -> str:
            m = re.search(label + r":\s*(.+)", text)
            return m.group(1).strip() if m else ""
        return {"vendor_name": text.split("-", 1)[-1].split("\n")[0].strip(),
                "invoice_number": grab("Invoice Number"), "invoice_date": grab("Invoice Date"),
                "due_date": grab("Due Date"), "amount_inr": grab("Amount Due").replace("INR", "").strip(),
                "po_number": grab("PO Number"), "notes": ""}

    def _p(self, tool: str, rationale: str = "planner", **args: Any) -> dict:
        p = {"tool_name": tool, "arguments": args, "action_rationale": rationale}
        self.proposals.append(p)
        return p

    def propose(self, s: TaskState) -> dict | None:
        if self.overrides:
            p = self.overrides.pop(0)
            self.proposals.append(p)
            return p
        done = [st.tool_name for st in s.executed_steps if st.status == "SUCCESS"]
        vendor = self.spec.vendor_name
        if any("REJECTED" in h for h in s.hints):
            return self._p("finish", outcome="not_entered", summary="A human rejected the entry.")
        if "search_vendor" not in done:
            return self._p("search_vendor", query=vendor)
        if not s.vendor_id:
            return self._p("list_invoices", vendor_id=VENDOR_IDS[vendor.lower()])
        latest = latest_by_date(s.candidate_invoices)
        assert latest
        if self.spec.mode == "report_only" and "search_internal_invoices" not in done:
            return self._p("search_internal_invoices", vendor_name=s.vendor_name, invoice_number=latest["invoice_number"])
        if self.spec.mode == "report_only":
            return self._p("finish", outcome="already_present" if s.erp_record else "not_entered", summary="checked")
        stale = (s.executed_steps[-1].status == "POLICY_BLOCKED"
                 and "changed since approval" in s.executed_steps[-1].summary)
        if not s.source_invoice_data or s.source_invoice_data["invoice_number"] != latest["invoice_number"] or stale:
            return self._p("extract_invoice", vendor_id=s.vendor_id, invoice_number=latest["invoice_number"])
        if s.executed_steps[-1].tool_name == "extract_invoice" or "search_internal_invoices" not in done:
            return self._p("search_internal_invoices", vendor_name=s.vendor_name,
                           invoice_number=latest["invoice_number"])
        if s.erp_record:
            origin = "entered" if s.erp_record.get("id") not in s.erp_baseline_ids else "already_present"
            return self._p("finish", outcome=origin, summary=f"{latest['invoice_number']} is in the ERP")
        src = s.source_invoice_data
        args: dict[str, Any] = {"vendor_name": s.vendor_name, "invoice_number": src["invoice_number"],
                                "invoice_date": src["invoice_date"], "due_date": src["due_date"],
                                "amount_inr": src["amount_inr"], "po_number": src["po_number"]}
        hint = next((h for h in reversed(s.hints) if "Controls on the page" in h), None)
        if hint and s.executed_steps[-1].outcome == "ADAPT":
            buttons = re.findall(r"'([^']+)'", hint.split("Controls on the page:")[1])
            args["ui_hints"] = {"submit_button_label": next(b for b in buttons if b != "Clear")}
        elif s.executed_steps[-1].outcome == "ADAPT" or (s.executed_steps and s.executed_steps[-1].arguments.get("ui_hints")):
            args["ui_hints"] = s.executed_steps[-1].arguments.get("ui_hints")
        return self._p("create_internal_invoice", **args)


def drive_to_end(engine, run_id: str, approve: bool | None = True, answer: str = "ok", max_rounds: int = 6):
    """Play the human: approve/reject approvals, answer questions, restart after crashes."""
    from src.runtime.engine import SimulatedCrash, mark_interrupted
    s = None
    for _ in range(max_rounds):
        try:
            s = engine.drive(run_id)
        except SimulatedCrash:
            mark_interrupted(engine.store, run_id, "simulated crash")
            continue
        if s.status.value == "WAITING_APPROVAL" and approve is not None:
            engine.decide(s.pending_approval_id, approve, "test")
            continue
        if s.status.value == "WAITING_HUMAN":
            engine.answer(s.pending_question_id, answer)
            continue
        break
    return s


def kinds(store: Store, run_id: str) -> list[str]:
    return [e["event_type"] for e in store.events(run_id)]


def today() -> str:
    return date.today().isoformat()
