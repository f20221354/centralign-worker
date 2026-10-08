"""Typed contracts shared by the agent, runtime, executors and verifier.

Money is always integer paise. Dates are ISO dates. Every model forbids unknown fields, so a proposal with an
extra argument (for example `human_approved=true`) is rejected before it reaches policy.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------- money


def to_paise(value: Any) -> int:
    """'₹46,250.50', '46250.5', 46250 -> 4625050. Never goes through float."""
    if isinstance(value, bool):
        raise ValueError("amount must be a number")
    if isinstance(value, int):
        return value * 100
    text = str(value).replace("₹", "").replace("INR", "").replace("Rs.", "").replace(",", "").strip()
    try:
        d = Decimal(text)
    except InvalidOperation as e:
        raise ValueError(f"not an amount: {value!r}") from e
    if d != d.quantize(Decimal("0.01")):
        raise ValueError(f"amount has more than 2 decimals: {value!r}")
    return int(d * 100)


def fmt_inr(paise: int) -> str:
    """4625050 -> '₹46,250.50' with Indian digit grouping (₹2,10,000.00)."""
    rupees, p = divmod(abs(paise), 100)
    s = str(rupees)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    return f"{'-' if paise < 0 else ''}₹{s}.{p:02d}"


def stable_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def idempotency_key(vendor_name: str, invoice_number: str) -> str:
    """One business invoice -> one key, whoever submits it and however often."""
    return hashlib.sha256(f"{norm(vendor_name)}|{norm(invoice_number)}".encode()).hexdigest()[:24]


def norm(s: str) -> str:
    return " ".join(str(s).lower().split())


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- goal


class GoalSpec(Strict):
    objective: str = Field(max_length=200)
    vendor_name: str = Field(min_length=2, max_length=80)
    invoice_selector: Literal["latest"] = "latest"
    mode: Literal["enter_if_missing", "report_only"] = "enter_if_missing"
    fields_to_extract: list[str] = Field(default_factory=lambda: ["amount", "due_date"])
    success_criteria: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    # A user may make approval stricter ("ask me before anything above ₹20,000"), never looser: policy takes the min.
    approval_threshold_paise: int | None = Field(default=None, gt=0)


# ---------------------------------------------------------------- tool arguments

VENDOR_ID = r"^V-[A-Z0-9]{2,12}$"
INVOICE_NO = r"^[A-Za-z0-9][A-Za-z0-9/_-]{1,30}$"


class SearchVendorArgs(Strict):
    query: str = Field(min_length=2, max_length=80, description="Vendor name or part of it")


class ListInvoicesArgs(Strict):
    vendor_id: str = Field(pattern=VENDOR_ID, description="Portal vendor id, e.g. V-ACME")


class OpenInvoiceArgs(Strict):
    vendor_id: str = Field(pattern=VENDOR_ID)
    invoice_number: str = Field(pattern=INVOICE_NO)


class ExtractInvoiceArgs(Strict):
    vendor_id: str = Field(pattern=VENDOR_ID)
    invoice_number: str = Field(pattern=INVOICE_NO)


class SearchInternalInvoicesArgs(Strict):
    vendor_name: str = Field(min_length=2, max_length=80)
    invoice_number: str | None = Field(default=None, pattern=INVOICE_NO)


class UIHints(Strict):
    submit_button_label: str | None = Field(default=None, max_length=40,
                                            description="Label of the form's submit button, if it differs")


class CreateInternalInvoiceArgs(Strict):
    vendor_name: str = Field(min_length=2, max_length=80)
    invoice_number: str = Field(pattern=INVOICE_NO)
    invoice_date: date
    due_date: date
    amount_inr: str = Field(description="Exact amount as printed, e.g. '46250.50'")
    po_number: str | None = Field(default=None, max_length=30)
    ui_hints: UIHints | None = None

    @field_validator("amount_inr")
    @classmethod
    def _amount(cls, v: str) -> str:
        if to_paise(v) <= 0:
            raise ValueError("amount must be positive")
        return v

    @model_validator(mode="after")
    def _dates(self) -> CreateInternalInvoiceArgs:
        if self.due_date < self.invoice_date:
            raise ValueError("due_date is before invoice_date")
        return self

    @property
    def amount_paise(self) -> int:
        return to_paise(self.amount_inr)

    def business_fields(self) -> dict[str, Any]:
        """What an approval is for. UI hints are how, not what, so they don't invalidate an approval."""
        return {"vendor_name": norm(self.vendor_name), "invoice_number": self.invoice_number,
                "invoice_date": self.invoice_date.isoformat(), "due_date": self.due_date.isoformat(),
                "amount_paise": self.amount_paise, "po_number": self.po_number}


class AskHumanArgs(Strict):
    question: str = Field(min_length=5, max_length=400)


class FinishArgs(Strict):
    outcome: Literal["entered", "already_present", "not_entered"]
    summary: str = Field(min_length=5, max_length=600)


ToolName = Literal["search_vendor", "list_invoices", "open_invoice", "extract_invoice",
                   "search_internal_invoices", "create_internal_invoice", "ask_human", "finish"]


class ActionProposal(Strict):
    """What the model may say. Nothing else reaches the runtime."""
    tool_name: ToolName
    arguments: dict[str, Any]
    action_rationale: str = Field(default="", max_length=300)


# ---------------------------------------------------------------- observations


class ObsStatus(StrEnum):
    SUCCESS = "SUCCESS"
    HTTP_ERROR = "HTTP_ERROR"            # 5xx, timeout, network: transient
    DOM_ERROR = "DOM_ERROR"              # expected element missing: the UI changed
    DATA_AMBIGUITY = "DATA_AMBIGUITY"    # sources disagree, several matches: needs a decision
    POLICY_BLOCKED = "POLICY_BLOCKED"    # the policy gate denied it
    VALIDATION_ERROR = "VALIDATION_ERROR"  # arguments or business data rejected: replan, never retry blindly
    CONFLICT = "CONFLICT"                # same invoice id, different financial data
    NOT_FOUND = "NOT_FOUND"


class Observation(Strict):
    action_name: str
    status: ObsStatus
    data: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    screenshot_path: str | None = None
    http_status: int | None = None
    channel: Literal["browser", "api", "file", "runtime"] = "runtime"

    def brief(self, limit: int = 700) -> str:
        body = json.dumps(self.data, default=str)
        if len(body) > limit:
            body = body[:limit] + "…"
        err = f" error={self.error_message}" if self.error_message else ""
        return f"{self.status.value}{err} data={body}"


class Outcome(StrEnum):
    CONTINUE = "CONTINUE"
    RETRY = "RETRY"
    ADAPT = "ADAPT"
    REPLAN = "REPLAN"
    HUMAN = "HUMAN"
    FAIL = "FAIL"


# ---------------------------------------------------------------- state


class RunStatus(StrEnum):
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    WAITING_HUMAN = "WAITING_HUMAN"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"   # the worker process died; resume reconciles


TERMINAL = {RunStatus.COMPLETED, RunStatus.FAILED}


class StepRecord(Strict):
    step_index: int
    action_id: str
    tool_name: str
    arguments: dict[str, Any]
    rationale: str
    status: str
    outcome: str
    summary: str
    attempts: int = 1


class TaskState(Strict):
    """The execution memory. The model's context is rebuilt from this every step."""
    run_id: str
    goal_text: str
    goal_spec: GoalSpec | None = None
    vendor_id: str | None = None
    vendor_name: str | None = None
    candidate_invoices: list[dict[str, Any]] = Field(default_factory=list)
    selected_invoice_id: str | None = None
    source_invoice_data: dict[str, Any] | None = None
    erp_record: dict[str, Any] | None = None
    erp_baseline_ids: list[int] = Field(default_factory=list)
    executed_steps: list[StepRecord] = Field(default_factory=list)
    hints: list[str] = Field(default_factory=list)          # runtime -> model: recovery guidance, denials
    human_answers: list[dict[str, str]] = Field(default_factory=list)
    injection_warnings: list[str] = Field(default_factory=list)
    pending_action: dict[str, Any] | None = None             # proposal waiting for approval / answer
    pending_approval_id: str | None = None
    pending_question_id: str | None = None
    adaptations: int = 0
    step_index: int = 0
    status: RunStatus = RunStatus.PLANNING
    is_terminal: bool = False
    final_summary: str | None = None
    finish_outcome: str | None = None
    verification: dict[str, Any] | None = None
    verify_rounds: int = 0
    scenario: dict[str, Any] = Field(default_factory=dict)


class ApprovalSnapshot(Strict):
    approval_id: str
    run_id: str
    action_id: str
    tool_name: str
    parameters: dict[str, Any]
    critical_state_facts: dict[str, Any]
    policy_version: str
    state_payload_hash: str
    requested_at: str
    status: Literal["PENDING", "APPROVED", "REJECTED", "CONSUMED", "STALE"] = "PENDING"
    decided_at: str | None = None
    note: str | None = None


class ExecutionEvent(Strict):
    run_id: str
    step_index: int
    attempt_index: int
    event_type: str
    payload: dict[str, Any]
    timestamp: str
