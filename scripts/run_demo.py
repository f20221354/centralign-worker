"""Run demo scenarios against a running CentrAlign server, playing the human where needed.

    python scripts/run_demo.py --scenario 1            # one scenario
    python scripts/run_demo.py --all --out runs/demo_results.json
    python scripts/run_demo.py --goal "..." --approve   # any goal

The human's role is explicit: approvals are approved (or rejected with --reject), questions get --answer,
and a crashed worker is restarted, exactly as an operator would in the console.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import httpx

SCENARIOS = {
    1: {"name": "Normal success (Acme Corp)", "task": "A", "flags": {}},
    2: {"name": "Transient failure: HTTP 500 on first ERP submit", "task": "A", "flags": {"inject_500": True}},
    3: {"name": "UI adaptation: ERP submit button renamed", "task": "A", "flags": {"rename_button": True}},
    4: {"name": "Human approval above ₹50,000 (Gamma Industries)", "task": "C", "flags": {}},
    5: {"name": "Prompt injection in the invoice PDF (Delta Supplies)", "task": "D", "flags": {}},
    6: {"name": "Crash after the ERP write, then restart (idempotency)", "task": "A",
        "flags": {"crash_after_submit": True}},
    7: {"name": "Generalization: already in ERP, enter only if missing (Beta Manufacturing)", "task": "B",
        "flags": {}},
}


def run_one(base: str, goal: str, flags: dict, approve: bool = True, answer: str = "Proceed as policy allows.",
            quiet: bool = False, timeout_s: int = 900) -> dict:
    h = httpx.Client(base_url=base, timeout=60)
    r = h.post("/api/runs", json={"goal": goal, **flags})
    r.raise_for_status()
    run_id, last, t0, handled = r.json()["run_id"], 0, time.time(), set()
    print(f"\n=== run {run_id}: {goal}\n    flags={flags}", flush=True)
    while time.time() - t0 < timeout_s:
        d = h.get(f"/api/runs/{run_id}", params={"after": last}).json()
        for e in d["events"]:
            last = e["id"]
            if not quiet:
                p = e["payload"]
                brief = {k: p[k] for k in ("tool", "decision", "reason", "status", "outcome", "strategy", "rationale",
                                           "invoice_number", "is_latest_by_date", "passed", "erp_id", "signals",
                                           "approval_id", "question")
                         if k in p and p[k] not in (None, "", [])}
                print(f"  {e['event_type']:<26} a{e['attempt_index']} {json.dumps(brief, ensure_ascii=False)[:230]}",
                      flush=True)
        a, q = d["pending_approval"], d["pending_question"]
        if a and a["approval_id"] not in handled and not d["alive"]:
            handled.add(a["approval_id"])
            print(f"  >>> HUMAN {'approves' if approve else 'rejects'} {a['parameters']['invoice_number']}", flush=True)
            h.post(f"/api/approvals/{a['approval_id']}", json={"approve": approve, "note": "demo operator"})
        elif q and q["question_id"] not in handled and not d["alive"]:
            handled.add(q["question_id"])
            print(f"  >>> HUMAN answers: {answer}", flush=True)
            h.post(f"/api/questions/{q['question_id']}", json={"answer": answer})
        elif d["status"] == "INTERRUPTED" and not d["alive"]:
            print("  >>> operator restarts the crashed worker", flush=True)
            time.sleep(1)
            h.post(f"/api/runs/{run_id}/resume", json={})
        elif d["status"] in ("COMPLETED", "FAILED") and not d["alive"]:
            v = d.get("verification") or {}
            print(f"=== {d['status']}  verification={'passed' if v.get('passed') else 'failed'}  "
                  f"steps={d['step_index']}  {time.time() - t0:.0f}s", flush=True)
            return {"run_id": run_id, "status": d["status"], "verification": v, "steps": d["step_index"],
                    "seconds": round(time.time() - t0), "events": [], "final_summary": d["final_summary"]}
        time.sleep(1.5)
    return {"run_id": run_id, "status": "TIMEOUT"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--scenario", type=int)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--goal")
    ap.add_argument("--reject", action="store_true")
    ap.add_argument("--answer", default="Proceed as company policy allows.")
    ap.add_argument("--out")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    examples = {e["id"]: e["goal"] for e in httpx.get(a.base + "/api/examples").json()}
    results = []
    if a.goal:
        results.append(run_one(a.base, a.goal, {}, not a.reject, a.answer, a.quiet))
    for n in (sorted(SCENARIOS) if a.all else [a.scenario] if a.scenario else []):
        sc = SCENARIOS[n]
        res = run_one(a.base, examples[sc["task"]], sc["flags"], not a.reject, a.answer, a.quiet)
        results.append({"scenario": n, "name": sc["name"], **res})
    if a.out:
        with open(a.out, "w") as f:
            json.dump(results, f, indent=1, default=str)
    return 0 if all(r["status"] == "COMPLETED" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
