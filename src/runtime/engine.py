"""The execution engine.

    proposal -> validation -> policy (-> time-of-use recheck when an approval is used) -> executor
             -> observation -> classifier -> state update -> event log -> next decision

There is exactly one path to an executor (`_process`). Adapted actions, actions resumed after approval and
actions retried after a crash all go through it again. Waiting for a human ends the drive; a decision or an
answer starts a new drive from the persisted state, the same code path crash recovery uses.
"""
from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from src.agent.agent import Agent
from src.agent.client import LLMUnavailable
from src.agent.tools import REGISTRY
from src.contracts import (
    TERMINAL,
    ActionProposal,
    ApprovalSnapshot,
    CreateInternalInvoiceArgs,
    Observation,
    ObsStatus,
    Outcome,
    RunStatus,
    StepRecord,
    TaskState,
    fmt_inr,
    idempotency_key,
    now_iso,
    stable_hash,
    to_paise,
)
from src.execution.api_executor import ERPAPI, PortalAPI, search_internal
from src.execution.browser_executor import BrowserExecutor
from src.execution.file_executor import injection_signals, pdf_text, save_pdf
from src.runtime import policy
from src.runtime.classifier import backoff_s, classify
from src.runtime.state import apply
from src.trace import Store, runs_dir
from src.verification.verifier import verify

MAX_STEPS = 24


class SimulatedCrash(BaseException):
    """Demo/test: the process 'dies' right after the ERP write, before the result is recorded."""


class Engine:
    def __init__(self, store: Store, agent: Agent, portal: PortalAPI | None = None, erp: ERPAPI | None = None,
                 browser_factory: Callable[[Path], Any] | None = None):
        self.store, self.agent = store, agent
        self.portal, self.erp = portal or PortalAPI(), erp or ERPAPI()
        self.browser_factory = browser_factory or BrowserExecutor
        self.browser: Any = None

    # ================================================================== entry points

    def start(self, goal_text: str, scenario: dict[str, Any] | None = None) -> str:
        run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        s = TaskState(run_id=run_id, goal_text=goal_text.strip(), scenario=scenario or {})
        self.store.save(s)
        self.ev(s, "GOAL_CREATED", {"goal": s.goal_text, "scenario": s.scenario})
        return run_id

    def drive(self, run_id: str) -> TaskState:
        """Run (or resume) until terminal or waiting for a human. SimulatedCrash propagates like a dead process."""
        s = self.store.load(run_id)
        self.browser = self.browser_factory(runs_dir() / run_id)
        try:
            if s.status in (RunStatus.INTERRUPTED, RunStatus.RUNNING):
                self._reconcile(s)
            if s.status == RunStatus.PLANNING:
                self._understand(s)
            if s.status == RunStatus.WAITING_APPROVAL and not self._after_approval(s):
                return s
            if s.status == RunStatus.WAITING_HUMAN and not self._after_answer(s):
                return s
            if s.status == RunStatus.PENDING_VERIFICATION:
                self._verify(s)
            self._loop(s)
        except SimulatedCrash:
            raise
        except LLMUnavailable as e:  # the model is unreachable: pause, keep everything, resume later
            s.status = RunStatus.INTERRUPTED
            self.ev(s, "WORKER_PAUSED", {"reason": str(e)[:400], "resume": "restart the worker when an LLM "
                                                                       "provider is available"})
            self.store.save(s)
        except Exception as e:  # noqa: BLE001 - a run must end in a recorded state, whatever broke
            self._fail(s, f"runtime error: {type(e).__name__}: {e}"[:400])
        finally:
            self.browser.close()
        return s

    # ================================================================== helpers

    def ev(self, s: TaskState, kind: str, payload: dict[str, Any] | None = None, attempt: int = 0) -> None:
        self.store.event(s.run_id, s.step_index, attempt, kind, payload)

    def _fail(self, s: TaskState, why: str) -> None:
        s.status, s.is_terminal = RunStatus.FAILED, True
        self.ev(s, "RUN_FAILED", {"reason": why})
        self.store.save(s)

    def _record(self, s: TaskState, action_id: str, prop: ActionProposal, status: str, outcome: str, summary: str,
                attempts: int = 1) -> None:
        s.executed_steps.append(StepRecord(step_index=s.step_index, action_id=action_id, tool_name=prop.tool_name,
                                           arguments=prop.arguments, rationale=prop.action_rationale, status=status,
                                           outcome=outcome, summary=summary[:400], attempts=attempts))

    # ================================================================== phases

    def _understand(self, s: TaskState) -> None:
        spec = self.agent.understand(s.goal_text)
        s.goal_spec = spec
        s.erp_baseline_ids = self.erp.all_ids()
        s.status = RunStatus.RUNNING
        self.ev(s, "GOAL_UNDERSTOOD", {"goal_spec": spec.model_dump(),
                                       "approval_threshold": fmt_inr(policy.threshold(s))})
        self.store.save(s)

    def _loop(self, s: TaskState) -> None:
        silent = 0
        while s.status == RunStatus.RUNNING and not s.is_terminal:
            if s.step_index >= MAX_STEPS:
                return self._fail(s, f"step budget of {MAX_STEPS} exhausted")
            raw = self.agent.propose(s)
            if raw is None:
                silent += 1
                s.hints.append("You replied without a tool call. Call exactly one tool.")
                if silent >= 2:
                    return self._fail(s, "the model stopped proposing actions")
                continue
            self._process(s, raw)
            self.store.save(s)

    # ================================================================== the pipeline

    def _process(self, s: TaskState, raw: dict[str, Any], origin: str | None = None) -> None:
        s.step_index += 1
        action_id = f"a{s.step_index}-{uuid.uuid4().hex[:6]}"
        self.ev(s, "ACTION_PROPOSED", {"action_id": action_id, "tool": raw.get("tool_name"),
                                       "arguments": raw.get("arguments"), "rationale": raw.get("action_rationale"),
                                       "model": origin or getattr(self.agent, "last_provider", None) or "planner"})
        # 1. validation: registered tool, typed arguments, nothing extra (e.g. no `human_approved`)
        try:
            prop = ActionProposal.model_validate(raw)
            args = REGISTRY[prop.tool_name][0].model_validate(prop.arguments)
        except ValidationError as e:
            errs = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:4])
            self.ev(s, "ACTION_REJECTED", {"action_id": action_id, "stage": "validation", "errors": errs})
            fake = ActionProposal.model_construct(tool_name=str(raw.get("tool_name")),  # type: ignore[arg-type]
                                                  arguments=raw.get("arguments") or {},
                                                  action_rationale=str(raw.get("action_rationale", "")))
            self._record(s, action_id, fake, "VALIDATION_ERROR", "REPLAN", errs)
            s.hints.append(f"Your proposal was rejected before execution: {errs}. Fix it.")
            return
        # 2. policy
        approvals = self.store.approvals(s.run_id)
        dec = policy.evaluate(prop.tool_name, args, s, approvals)
        if dec.kind == "ALLOW" and dec.approval_id:  # executing on an approval: re-check live facts now
            snap = self.store.approval(dec.approval_id)
            assert snap is not None and isinstance(args, CreateInternalInvoiceArgs)
            live = self._live_facts(s, args)
            self.ev(s, "APPROVAL_REVALIDATION", {"approval_id": snap.approval_id, "live_facts": live,
                                                 "approved_facts": snap.critical_state_facts})
            dec = policy.revalidate(snap, live, args, s, approvals)
            if dec.kind != "ALLOW":
                snap.status = "STALE"
                self.store.put_approval(snap)
                self.ev(s, "APPROVAL_INVALIDATED", {"approval_id": snap.approval_id, "reason": dec.reason})
                s.hints.append(f"The approval is no longer valid ({dec.reason}). Re-read the source data; a new "
                               "approval will be requested if one is still needed.")
        self.ev(s, "POLICY_EVALUATED", {"action_id": action_id, "tool": prop.tool_name, **dec.as_dict()})
        if dec.kind == "DENY":
            self._record(s, action_id, prop, ObsStatus.POLICY_BLOCKED.value, Outcome.REPLAN.value, dec.reason)
            s.hints.append(f"Policy blocked {prop.tool_name}: {dec.reason}")
            return
        if dec.kind == "REQUIRE_APPROVAL":
            assert isinstance(args, CreateInternalInvoiceArgs)
            return self._request_approval(s, action_id, prop, args, dec.reason)
        # 3. runtime tools
        if prop.tool_name == "ask_human":
            self._record(s, action_id, prop, "WAITING_HUMAN", Outcome.HUMAN.value, args.question)  # type: ignore[attr-defined]
            return self._ask(s, args.question)  # type: ignore[attr-defined]
        if prop.tool_name == "finish":
            return self._finish(s, action_id, prop, args)
        # 4. execute, observe, classify, recover
        self._execute_with_recovery(s, action_id, prop, args, dec.approval_id)

    def _execute_with_recovery(self, s: TaskState, action_id: str, prop: ActionProposal, args: BaseModel,
                               approval_id: str | None) -> None:
        tool = prop.tool_name
        side_effect = tool in policy.SIDE_EFFECT_TOOLS
        key = (idempotency_key(args.vendor_name, args.invoice_number)  # type: ignore[attr-defined]
               if side_effect else None)
        attempt = 0
        while True:
            attempt += 1
            self.ev(s, "ACTION_STARTED", {"action_id": action_id, "tool": tool, "side_effect": side_effect,
                                          "idempotency_key": key, "channel": REGISTRY[tool][2]}, attempt)
            if side_effect:
                self.store.save(s)  # persisted intent: a crash after this point is reconcilable
            obs = self._execute(s, tool, args, key)
            shot = Path(obs.screenshot_path).name if obs.screenshot_path else None
            self.ev(s, "OBSERVATION_RECORDED", {"action_id": action_id, **obs.model_dump(mode="json"),
                                                "screenshot": shot}, attempt)
            outcome, why = classify(obs, attempt, s.adaptations)
            self.ev(s, "OUTCOME_CLASSIFIED", {"action_id": action_id, "status": obs.status.value,
                                              "outcome": outcome.value, "why": why}, attempt)
            if outcome != Outcome.RETRY:
                break
            wait = backoff_s(attempt)
            self.ev(s, "RECOVERY_ATTEMPTED", {"action_id": action_id, "strategy": "retry", "next_attempt":
                                              attempt + 1, "backoff_s": wait, "reason": obs.error_message}, attempt)
            time.sleep(wait)
        for note in apply(s, tool, prop.arguments, obs):
            if note == "INVOICE_SELECTED":
                self.ev(s, "INVOICE_SELECTED", {**obs.data["_selection"], "rationale": prop.action_rationale})
            if note == "ALREADY_IN_ERP":
                self.ev(s, "REPLANNED", {"reason": f"{s.selected_invoice_id} is already in the ERP (record "
                                                   f"#{s.erp_record and s.erp_record.get('id')}); no entry needed"})
                s.hints.append(f"{s.selected_invoice_id} is already in the ERP. Do not create it again.")
        self._record(s, action_id, prop, obs.status.value, outcome.value, obs.brief(500), attempt)
        if obs.status == ObsStatus.SUCCESS:
            self.ev(s, "ACTION_COMPLETED", {"action_id": action_id, "tool": tool, "attempts": attempt,
                                            "screenshot": shot})
            if approval_id and (snap := self.store.approval(approval_id)):
                snap.status = "CONSUMED"
                self.store.put_approval(snap)
        if outcome == Outcome.ADAPT:
            s.adaptations += 1
            self.ev(s, "RECOVERY_ATTEMPTED", {"action_id": action_id, "strategy": "adapt",
                                              "observed": obs.data, "reason": obs.error_message})
            s.hints.append(f"The page changed: {obs.error_message}. Controls on the page: "
                           f"{obs.data.get('available_buttons')}. Propose {tool} again with ui_hints naming the "
                           "equivalent control. It will be validated and policy-checked again.")
        elif outcome == Outcome.REPLAN:
            self.ev(s, "REPLAN_REQUIRED", {"action_id": action_id, "reason": why, "error": obs.error_message})
            s.hints.append(f"{tool} returned {obs.status.value}: {obs.error_message or why}. Choose another action.")
        elif outcome == Outcome.HUMAN:
            self._ask(s, f"{tool} returned {obs.status.value}: {obs.error_message or why}. How should I proceed?")
        elif outcome == Outcome.FAIL:
            self._fail(s, f"{tool}: {why}: {obs.error_message or ''}")

    def _execute(self, s: TaskState, tool: str, args: Any, key: str | None) -> Observation:
        if tool == "search_vendor":
            return self.browser.search_vendor(args.query)
        if tool == "list_invoices":
            return self.browser.list_invoices(args.vendor_id)
        if tool == "open_invoice":
            return self.browser.open_invoice(args.vendor_id, args.invoice_number)
        if tool == "extract_invoice":
            return self._extract(s, args.vendor_id, args.invoice_number)
        if tool == "search_internal_invoices":
            return search_internal(self.erp, args.vendor_name, args.invoice_number)
        if tool == "create_internal_invoice":
            crash = bool(s.scenario.get("crash_after_submit")) and not s.scenario.get("crash_done")
            if crash:
                s.scenario["crash_done"] = True
                self.store.save(s)

            def after_submit() -> None:
                if crash:
                    if os.environ.get("CRASH_HARD") == "1":
                        os._exit(1)
                    raise SimulatedCrash()
            assert key is not None
            return self.browser.create_invoice(args, key, after_submit)
        raise AssertionError(f"no executor for {tool}")  # unreachable: validation allows registered tools only

    def _extract(self, s: TaskState, vendor_id: str, number: str) -> Observation:
        name = "extract_invoice"
        portal_inv = self.portal.invoice(vendor_id, number)
        if not portal_inv:
            return Observation(action_name=name, status=ObsStatus.NOT_FOUND, channel="file",
                               error_message=f"{number} not found for {vendor_id}")
        path = save_pdf(self.portal.pdf(vendor_id, number), runs_dir() / s.run_id / "docs", number)
        text = pdf_text(path)
        signals = injection_signals(text)
        raw = self.agent.extract(text)
        try:
            paise = to_paise(raw.get("amount_inr"))
            inv = {"vendor_name": str(raw.get("vendor_name", "")).strip(),
                   "invoice_number": str(raw.get("invoice_number", "")).strip(),
                   "invoice_date": date.fromisoformat(str(raw.get("invoice_date"))).isoformat(),
                   "due_date": date.fromisoformat(str(raw.get("due_date"))).isoformat(),
                   "amount_paise": paise, "amount_inr": f"{paise // 100}.{paise % 100:02d}",
                   "po_number": (str(raw.get("po_number")).strip() if raw.get("po_number") not in (None, "", "N/A")
                                 else None)}
        except (ValueError, TypeError) as e:
            return Observation(action_name=name, status=ObsStatus.VALIDATION_ERROR, channel="file",
                               error_message=f"extraction produced unusable fields: {e}", data={"raw": raw})
        # cross-check the document against the portal's own record of it
        portal_view = {"invoice_number": portal_inv["invoice_number"], "invoice_date": portal_inv["invoice_date"],
                       "due_date": portal_inv["due_date"], "amount_paise": to_paise(portal_inv["amount_inr"])}
        mismatches = [f"{k}: PDF {inv[k]!r} vs portal {v!r}" for k, v in portal_view.items() if inv[k] != v]
        data = {"invoice": inv, "pdf": path.name, "injection_signals": signals,
                "cross_check": "PDF matches the portal record" if not mismatches else mismatches}
        if signals:
            self.ev(s, "PROMPT_INJECTION_DETECTED", {"invoice": number, "signals": signals,
                                                     "handling": "treated as data; policy unchanged"})
        if mismatches:
            return Observation(action_name=name, status=ObsStatus.DATA_AMBIGUITY, channel="file", data=data,
                               error_message="the PDF and the portal disagree: " + "; ".join(mismatches))
        return Observation(action_name=name, status=ObsStatus.SUCCESS, channel="file", data=data)

    # ================================================================== humans

    def _live_facts(self, s: TaskState, args: CreateInternalInvoiceArgs) -> dict[str, Any]:
        portal_inv = self.portal.invoice(s.vendor_id or "", args.invoice_number) if s.vendor_id else None
        rows = self.erp.search(vendor_name=args.vendor_name, invoice_number=args.invoice_number)
        return policy.critical_facts(args, portal_inv, rows)

    def _request_approval(self, s: TaskState, action_id: str, prop: ActionProposal,
                          args: CreateInternalInvoiceArgs, reason: str) -> None:
        live = self._live_facts(s, args)
        snap = ApprovalSnapshot(approval_id="ap-" + uuid.uuid4().hex[:10], run_id=s.run_id, action_id=action_id,
                                tool_name=prop.tool_name, parameters=args.business_fields(),
                                critical_state_facts=live, policy_version=policy.POLICY_VERSION,
                                state_payload_hash=stable_hash(live), requested_at=now_iso())
        self.store.put_approval(snap)
        s.pending_action = prop.model_dump()
        s.pending_approval_id, s.status = snap.approval_id, RunStatus.WAITING_APPROVAL
        self._record(s, action_id, prop, "WAITING_APPROVAL", Outcome.HUMAN.value, reason)
        self.ev(s, "APPROVAL_REQUESTED", {
            "approval_id": snap.approval_id, "action": "Create this invoice in ERP", "reason": reason,
            "vendor": args.vendor_name, "invoice": args.invoice_number, "amount": fmt_inr(args.amount_paise),
            "invoice_date": args.invoice_date.isoformat(), "due_date": args.due_date.isoformat(),
            "policy_version": policy.POLICY_VERSION, "state_payload_hash": snap.state_payload_hash[:16]})
        self.store.save(s)

    def decide(self, approval_id: str, approve: bool, note: str = "") -> ApprovalSnapshot:
        """Called by the console (a human), never by the model."""
        snap = self.store.approval(approval_id)
        if not snap or snap.status != "PENDING":
            raise ValueError("no pending approval with that id")
        snap.status, snap.decided_at, snap.note = ("APPROVED" if approve else "REJECTED"), now_iso(), note[:200]
        self.store.put_approval(snap)
        s = self.store.load(snap.run_id)
        self.ev(s, "APPROVAL_GRANTED" if approve else "APPROVAL_REJECTED",
                {"approval_id": approval_id, "note": snap.note})
        return snap

    def _after_approval(self, s: TaskState) -> bool:
        snap = self.store.approval(s.pending_approval_id or "")
        if not snap or snap.status == "PENDING":
            return False
        pending, s.pending_action, s.pending_approval_id = s.pending_action, None, None
        s.status = RunStatus.RUNNING
        if snap.status == "REJECTED":
            s.hints.append(f"A human REJECTED entering {snap.parameters.get('invoice_number')} "
                           f"({snap.note or 'no note'}). Do not enter it. Finish with outcome not_entered.")
            self.store.save(s)
            return True
        assert pending is not None
        # the approved proposal goes through validation, policy and revalidation again
        self._process(s, pending, origin="approved proposal, resubmitted by the runtime")
        self.store.save(s)
        return True

    def _ask(self, s: TaskState, question: str) -> None:
        qid = self.store.ask(s.run_id, question)
        s.pending_question_id, s.status = qid, RunStatus.WAITING_HUMAN
        self.ev(s, "CLARIFICATION_REQUESTED", {"question_id": qid, "question": question})
        self.store.save(s)

    def answer(self, question_id: str, text: str) -> None:
        q = self.store.question(question_id)
        if not q or q["status"] != "PENDING":
            raise ValueError("no pending question with that id")
        self.store.answer(question_id, text[:500])
        self.ev(self.store.load(q["run_id"]), "CLARIFICATION_RESOLVED", {"question_id": question_id, "answer": text[:500]})

    def _after_answer(self, s: TaskState) -> bool:
        q = self.store.question(s.pending_question_id or "")
        if not q or q["status"] != "ANSWERED":
            return False
        s.human_answers.append({"question": q["question"], "answer": q["answer"]})
        s.pending_question_id, s.status = None, RunStatus.RUNNING
        self.store.save(s)
        return True

    # ================================================================== completion

    def _finish(self, s: TaskState, action_id: str, prop: ActionProposal, args: Any) -> None:
        self._record(s, action_id, prop, "PENDING_VERIFICATION", Outcome.CONTINUE.value, args.summary)
        s.final_summary, s.finish_outcome = args.summary, args.outcome
        s.status = RunStatus.PENDING_VERIFICATION
        self.ev(s, "AGENT_REPORTED_DONE", {"outcome": args.outcome, "summary": args.summary,
                                           "note": "not trusted until verified"})
        self.store.save(s)
        self._verify(s)

    def _verify(self, s: TaskState) -> None:
        result = verify(s, self.store.approvals(s.run_id), self.portal, self.erp)
        s.verification = result
        s.verify_rounds += 1
        self.ev(s, "VERIFICATION_COMPLETED", result)
        if result["passed"]:
            s.status, s.is_terminal = RunStatus.COMPLETED, True
            self.ev(s, "RUN_COMPLETED", result["evidence"])
        elif s.verify_rounds < 2:  # one chance to fix what the verifier found, through the normal pipeline
            failed = [f"{c['name']} ({c['detail']})" for c in result["checks"] if not c["passed"]]
            s.status = RunStatus.RUNNING
            s.hints.append("Independent verification FAILED: " + "; ".join(failed) + ". Fix it, then finish again.")
            self.ev(s, "REPLAN_REQUIRED", {"reason": "verification failed", "failed_checks": failed})
        else:
            self._fail(s, "verification failed")
        self.store.save(s)

    # ================================================================== crash recovery

    def _reconcile(self, s: TaskState) -> None:
        """Side effects started but never recorded are looked up in the ERP by idempotency key."""
        events = self.store.events(s.run_id)
        observed = {(e["payload"].get("action_id"), e["attempt_index"]) for e in events
                    if e["event_type"] == "OBSERVATION_RECORDED"}
        dangling = [e for e in events if e["event_type"] == "ACTION_STARTED" and e["payload"].get("side_effect")
                    and (e["payload"]["action_id"], e["attempt_index"]) not in observed]
        reconciled = {e["payload"].get("action_id") for e in events if e["event_type"] in
                      ("ACTION_RECONCILED", "SIDE_EFFECT_NOT_FOUND")}
        dangling = [e for e in dangling if e["payload"]["action_id"] not in reconciled]
        was = s.status
        self.ev(s, "WORKER_RESUMED", {"previous_status": was.value, "unrecorded_side_effects": len(dangling)})
        for e in dangling:
            p = e["payload"]
            rows = self.erp.search(idempotency_key=p["idempotency_key"])
            if rows:
                s.erp_record = rows[0]
                self.ev(s, "ACTION_RECONCILED", {"action_id": p["action_id"], "idempotency_key": p["idempotency_key"],
                                                 "erp_id": rows[0]["id"], "conclusion": "the write happened before "
                                                 "the crash; it will not be repeated"})
                s.hints.append(f"Crash recovery: this run's create action DID reach the ERP before the crash "
                               f"(record #{rows[0]['id']}, found by idempotency key). Do not create it again.")
                for a in self.store.approvals(s.run_id):
                    if a.status == "APPROVED" and a.parameters.get("invoice_number") == rows[0]["invoice_number"]:
                        a.status = "CONSUMED"
                        self.store.put_approval(a)
            else:
                self.ev(s, "SIDE_EFFECT_NOT_FOUND", {"action_id": p["action_id"],
                                                     "idempotency_key": p["idempotency_key"]})
                s.hints.append("A create attempt before the crash never reached the ERP; it is safe to propose again.")
        if was in (RunStatus.INTERRUPTED, RunStatus.RUNNING):
            s.status = RunStatus.RUNNING if s.goal_spec else RunStatus.PLANNING
            if s.pending_approval_id:
                s.status = RunStatus.WAITING_APPROVAL
            elif s.pending_question_id:
                s.status = RunStatus.WAITING_HUMAN
        self.store.save(s)


def mark_interrupted(store: Store, run_id: str, reason: str) -> None:
    """Supervisor: the worker died. Record it from outside, as a process monitor would."""
    s = store.load(run_id)
    if s.status not in TERMINAL:
        s.status = RunStatus.INTERRUPTED
        store.save(s)
        store.event(run_id, s.step_index, 0, "WORKER_CRASHED", {"reason": reason})
