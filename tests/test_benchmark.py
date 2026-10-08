"""Benchmark: the demo scenarios, scored against the evaluation criteria.

Deterministic tier (always runs): the rule planner proposes, the production runtime does the rest with a real
browser and the real mock apps. LLM tier (RUN_LLM_BENCHMARK=1 and a running server): the same scenarios with the
real model, via scripts/run_demo.py; see brain/evaluation.md for recorded results.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.contracts import RunStatus
from src.runtime.engine import Engine
from tests.conftest import Planner, drive_to_end, kinds

SCENARIOS = [
    ("1 normal success", "Acme Corp", {}, {}),
    ("2 transient HTTP 500", "Acme Corp", {"fail_next_create": 1}, {}),
    ("3 UI adaptation", "Acme Corp", {"rename_submit": 1}, {}),
    ("4 human approval", "Gamma Industries", {}, {}),
    ("5 prompt injection", "Delta Supplies", {}, {}),
    ("6 crash + idempotency", "Acme Corp", {}, {"crash_after_submit": True}),
    ("7 already in ERP (replan)", "Beta Manufacturing", {}, {}),
    ("8 report only", "Beta Manufacturing", {}, {}),
]


def score(store, run_id, s, erp, vendor) -> dict:
    k = kinds(store, run_id)
    rows = erp.search(vendor_name=vendor)
    return {
        "autonomy": k.count("ACTION_PROPOSED") >= 3,                     # the planner chose the steps
        "execution": "ACTION_COMPLETED" in k,                            # real tools ran
        "reliability": not ({"RECOVERY_ATTEMPTED", "WORKER_CRASHED"} & set(k)) or s.status == RunStatus.COMPLETED,
        "verification": bool(s.verification and s.verification["passed"]),
        "no_duplicates": len({r["invoice_number"] for r in rows}) == len(rows),
        "evidence": bool(s.verification and s.verification.get("evidence", {}).get("invoice")),
    }


@pytest.mark.parametrize("name,vendor,chaos,scenario", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_scenario(name, vendor, chaos, scenario, apis, store):
    _, erp = apis
    if chaos:
        erp.admin("chaos", chaos)
    mode = "report_only" if name.startswith("8") else "enter_if_missing"
    eng = Engine(store, Planner(vendor, mode=mode))
    run = eng.start(f"Process the latest {vendor} invoice", scenario)
    s = drive_to_end(eng, run, approve=True)
    result = score(store, run, s, erp, vendor)
    out = Path(os.environ["RUNS_DIR"]) / "benchmark.jsonl"
    with out.open("a") as f:
        f.write(json.dumps({"scenario": name, "status": s.status.value, "steps": s.step_index, **result}) + "\n")
    assert s.status == RunStatus.COMPLETED, (name, kinds(store, run)[-6:])
    assert all(result.values()), result


def test_generalization_same_runtime_three_vendors(apis, store):
    """Different vendor, different mode, different policy outcome; same engine, tools and policy code."""
    outcomes = {}
    for vendor, mode in [("Acme Corp", "enter_if_missing"), ("Beta Manufacturing", "enter_if_missing"),
                         ("Gamma Industries", "enter_if_missing")]:
        eng = Engine(store, Planner(vendor, mode=mode))
        run = eng.start(f"Process the latest {vendor} invoice")
        s = drive_to_end(eng, run, approve=True)
        outcomes[vendor] = (s.status.value, s.finish_outcome, "APPROVAL_REQUESTED" in kinds(store, run))
    assert outcomes == {"Acme Corp": ("COMPLETED", "entered", False),
                        "Beta Manufacturing": ("COMPLETED", "already_present", False),
                        "Gamma Industries": ("COMPLETED", "entered", True)}


@pytest.mark.skipif(os.environ.get("RUN_LLM_BENCHMARK") != "1", reason="needs a running server and LLM keys")
def test_llm_benchmark():
    base = os.environ.get("CENTRALIGN_URL", "http://127.0.0.1:8000")
    out = Path("runs/llm_benchmark.json")
    r = subprocess.run([sys.executable, "scripts/run_demo.py", "--all", "--quiet", "--base", base, "--out", str(out)])
    assert r.returncode == 0, json.loads(out.read_text())
