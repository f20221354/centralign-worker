"""Record the demo: the real console driving real runs, plus the landing page and the ERP afterwards.

Prerequisite: a CentrAlign server started with RECORD_DIR=scripts/demo_video/out/agent (so the worker's own
browser is recorded too) and BROWSER_SLOWMO=300 (so its typing is visible). Then:

    python scripts/demo_video/record.py [--base http://127.0.0.1:8000]
    python scripts/demo_video/assemble.py

Writes one webm per scene to scripts/demo_video/out/ and a timeline.json with the run events of each scene
(times relative to the scene's video), which assemble.py uses to decide what plays at 1x and what is sped up.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import httpx
from playwright.sync_api import Page, sync_playwright

OUT = Path(__file__).parent / "out"
VIEW = {"width": 1440, "height": 900}
WEB = Path(__file__).resolve().parents[2] / "web" / "index.html"

CAPTION_JS = """
(() => {
  const mk = () => { if (document.getElementById('__cap') || !document.body) return;
    const d = document.createElement('div'); d.id = '__cap';
    d.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:99999;padding:16px 32px;font:600 24px/1.35 -apple-system,Helvetica,Arial,sans-serif;color:#fff;background:rgba(8,12,24,.88);text-align:center;transition:opacity .25s;opacity:0';
    document.body.appendChild(d); };
  window.setCap = t => { mk(); const d = document.getElementById('__cap'); d.textContent = t; d.style.opacity = t ? 1 : 0; };
  document.addEventListener('DOMContentLoaded', mk);
})();
"""


class Scene:
    def __init__(self, pw, name: str):
        self.name = name
        self.browser = pw.chromium.launch()
        self.ctx = self.browser.new_context(viewport=VIEW, record_video_dir=str(OUT / name), record_video_size=VIEW)
        self.ctx.add_init_script(CAPTION_JS)
        self.page: Page = self.ctx.new_page()
        self.t0 = time.time()
        self.marks: list[tuple[str, float]] = []
        self.run_ids: list[str] = []

    def now(self) -> float:
        return round(time.time() - self.t0, 2)

    def mark(self, label: str) -> None:
        self.marks.append((label, self.now()))

    def cap(self, text: str) -> None:
        self.page.evaluate("t => window.setCap && window.setCap(t)", text)

    def close(self, api: httpx.Client) -> dict:
        self.ctx.close()
        self.browser.close()
        vid = next((OUT / self.name).glob("*.webm"))
        vid.rename(OUT / f"{self.name}.webm")
        (OUT / self.name).rmdir()
        events = []
        for rid in self.run_ids:
            for e in api.get(f"/api/runs/{rid}").json()["events"]:
                t = datetime.fromisoformat(e["timestamp"]).timestamp() - self.t0
                events.append({"t": round(t, 2), "type": e["event_type"], "payload": e["payload"]})
        return {"marks": self.marks, "events": events}


def smooth_scroll(page: Page, y: float, ms: int = 1400) -> None:
    page.evaluate("""([y, ms]) => new Promise(r => { const s = scrollY, t0 = performance.now();
      const f = n => { const k = Math.min(1, (n - t0) / ms); scrollTo(0, s + (y - s) * (1 - Math.cos(k * Math.PI)) / 2);
        k < 1 ? requestAnimationFrame(f) : r(); }; requestAnimationFrame(f); })""", [y, ms])


def to(page: Page, sel: str, ms: int = 1400) -> None:
    smooth_scroll(page, page.evaluate(f"document.querySelector('{sel}').getBoundingClientRect().top + scrollY - 70"), ms)


def landing(sc: Scene) -> None:
    p = sc.page
    p.goto(WEB.as_uri())
    p.wait_for_timeout(1500)
    sc.cap("CentrAlign: give the worker the goal. It figures out the work.")
    sc.mark("hero")
    p.wait_for_timeout(8000)
    to(p, "#how")
    sc.cap("A real loop: decide → validate → policy → execute → observe → recover → verify")
    sc.mark("how")
    p.wait_for_timeout(9000)
    to(p, "#trust")
    sc.cap("The model proposes. Deterministic code decides.")
    sc.mark("trust")
    p.wait_for_timeout(9000)
    to(p, "#architecture")
    sc.cap("Worker runtime with a real browser; mock vendor portal and ERP")
    sc.mark("arch")
    p.wait_for_timeout(8000)


def console_run(sc: Scene, base: str, api: httpx.Client, example: str, toggles: list[str],
                captions: dict[str, str], approve: bool = False, restart: bool = False) -> None:
    p = sc.page
    p.goto(base + "/console")
    p.evaluate("sessionStorage.clear()")
    p.wait_for_timeout(1200)
    p.select_option("#examples", example)
    p.click("#loadEx")
    sc.cap(captions.get("intro", ""))
    p.wait_for_timeout(2500)
    for t in toggles:
        p.check(f"#{t}")
        p.wait_for_timeout(700)
    known = {r["run_id"] for r in api.get("/api/runs").json()}
    p.click("#run")
    sc.mark("run_clicked")
    rid = None
    while rid is None:
        p.wait_for_timeout(500)
        rid = next((r["run_id"] for r in api.get("/api/runs").json() if r["run_id"] not in known), None)
    sc.run_ids.append(rid)
    seen: set[str] = set()
    deadline = time.time() + 25 * 60
    while time.time() < deadline:
        p.wait_for_timeout(1000)
        d = api.get(f"/api/runs/{rid}").json()
        for e in d["events"]:
            k = e["event_type"]
            if k in captions and k not in seen:
                seen.add(k)
                sc.cap(captions[k])
        if approve and d["pending_approval"] and not d["alive"] and "approved" not in seen:
            seen.add("approved")
            p.evaluate("document.getElementById('approvalCard').scrollIntoView({block:'center',behavior:'smooth'})")
            sc.cap(captions.get("APPROVAL_REQUESTED", ""))
            p.wait_for_timeout(5500)
            sc.mark("approve_click")
            p.click("#approve")
            p.wait_for_timeout(1500)
            p.evaluate("scrollTo({top:0,behavior:'smooth'})")
        if restart and d["status"] == "INTERRUPTED" and not d["alive"] and "restarted" not in seen:
            seen.add("restarted")
            p.evaluate("document.getElementById('crashCard').scrollIntoView({block:'center',behavior:'smooth'})")
            sc.cap(captions.get("WORKER_CRASHED", ""))
            p.wait_for_timeout(5500)
            sc.mark("restart_click")
            p.click("#resume")
            p.wait_for_timeout(1500)
            p.evaluate("scrollTo({top:0,behavior:'smooth'})")
        if d["status"] in ("COMPLETED", "FAILED") and not d["alive"]:
            break
    p.wait_for_timeout(2500)
    sc.mark("done")
    sc.cap(captions.get("done", ""))
    p.evaluate("document.getElementById('evidenceCard').scrollIntoView({block:'start',behavior:'smooth'})")
    p.wait_for_timeout(7000)
    smooth_scroll(p, p.evaluate("scrollY") + 520, 2200)
    p.wait_for_timeout(6000)
    sc.mark("end")


def erp_after(sc: Scene, base: str) -> None:
    p = sc.page
    p.goto(base + "/erp/invoices")
    if "/login" in p.url:
        p.get_by_label("Username").fill("ops")
        p.get_by_label("Password").fill("northwind-demo")
        p.get_by_role("button", name="Sign in").click()
        p.wait_for_load_state()
    sc.cap("The ERP afterwards: one record per invoice, entered through its own form")
    sc.mark("erp")
    p.wait_for_timeout(9000)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--only", default="landing,run_failures,run_approval,run_crash,erp")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    api = httpx.Client(base_url=a.base, timeout=30)
    timeline = json.loads((OUT / "timeline.json").read_text()) if (OUT / "timeline.json").exists() else {}
    with sync_playwright() as pw:
        for name in a.only.split(","):
            sc = Scene(pw, name)
            if name == "landing":
                landing(sc)
            elif name == "run_failures":
                console_run(sc, a.base, api, "A", ["inject_500", "rename_button"], {
                    "intro": "One plain-language goal. Two failures injected into the ERP.",
                    "GOAL_UNDERSTOOD": "The worker turns the sentence into a goal spec, then decides each step itself",
                    "INVOICE_SELECTED": "Latest by date: INV-1042, not INV-1045 (higher number, older)",
                    "RECOVERY_ATTEMPTED": "Failure observed → classified → recovery chosen",
                    "VERIFICATION_COMPLETED": "Independent verifier checks the ERP against the portal",
                    "done": "Verified. Evidence and every check are shown"})
            elif name == "run_approval":
                console_run(sc, a.base, api, "C", [], {
                    "intro": "Different vendor, different constraint: ask me above ₹50,000",
                    "INVOICE_SELECTED": "GI-7781 is the latest Gamma invoice: ₹75,000",
                    "APPROVAL_REQUESTED": "Policy pauses the run. Only a human can approve, never the model",
                    "APPROVAL_REVALIDATION": "Before writing, the facts are re-read live (time-of-use check)",
                    "VERIFICATION_COMPLETED": "Verifier also checks an approval is on record",
                    "done": "Verified, with the approval on record"}, approve=True)
            elif name == "run_crash":
                console_run(sc, a.base, api, "A", ["crash_after_submit"], {
                    "intro": "Now the worker will die right after the ERP write",
                    "WORKER_CRASHED": "Crashed after the write, before recording it. Restart:",
                    "ACTION_RECONCILED": "Found by idempotency key: the write happened, so it is not repeated",
                    "VERIFICATION_COMPLETED": "Verifier: exactly one record, no duplicate",
                    "done": "Recovered and verified, with no duplicate"}, restart=True)
            elif name == "erp":
                erp_after(sc, a.base)
            timeline[name] = sc.close(api)
            (OUT / "timeline.json").write_text(json.dumps(timeline, indent=1, default=str))
            print(name, "recorded", flush=True)


if __name__ == "__main__":
    main()
