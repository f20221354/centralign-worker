"""Cut the recorded scenes into the demo video (H.264 MP4) with captions already burned in and a voice-over.

Key moments come from the run's own event log (timeline.json): they play at 1x with narration, and the waiting
between them is sped up. Needs ffmpeg and macOS `say` (any TTS that writes an audio file would do).

    python scripts/demo_video/assemble.py            -> scripts/demo_video/out/centralign-demo.mp4
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright

D = Path(__file__).parent / "out"
W = D / "work"
W.mkdir(parents=True, exist_ok=True)
VOICE, RATE, SIZE = "Samantha", "182", (1280, 800)
TL = json.loads((D / "timeline.json").read_text())


def sh(*a: str) -> None:
    subprocess.run(a, check=True, capture_output=True)


def dur(p: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                                capture_output=True, text=True).stdout)


def say_len(text: str, n: int) -> tuple[Path | None, float]:
    if not text:
        return None, 0.0
    a = W / f"a{n:03d}.aiff"
    sh("say", "-v", VOICE, "-r", RATE, "-o", str(a), text)
    return a, dur(a)


CARD = """<html><body style="margin:0;width:1280px;height:800px;display:flex;flex-direction:column;justify-content:center;
padding:0 120px;box-sizing:border-box;background:radial-gradient(circle at 20% 20%,#0f3b3a,#071014 70%);color:#eef2f5;
font-family:-apple-system,Helvetica,Arial,sans-serif">{}</body></html>"""
CARDS = {
    "title": """<div style="font:600 20px/1 sans-serif;letter-spacing:.2em;color:#2dd4bf">AUTONOMOUS AI TASK WORKER</div>
<div style="font:800 84px/1.05 sans-serif;margin:24px 0;letter-spacing:-.03em">CentrAlign</div>
<div style="font:400 32px/1.4 sans-serif;color:#b9c6d3;max-width:960px">Give the worker the goal.<br>It figures out the work.</div>""",
    "browser": """<div style="font:600 20px/1 sans-serif;letter-spacing:.2em;color:#2dd4bf">SAME RUN, THE WORKER'S OWN SCREEN</div>
<div style="font:800 60px/1.1 sans-serif;margin:22px 0">A real browser, driven by the worker</div>
<div style="font:400 28px/1.45 sans-serif;color:#b9c6d3">Portal search · invoice list · ERP form · the renamed button · the HTTP 500 · the retry</div>""",
    "close": """<div style="font:800 56px/1.1 sans-serif;margin-bottom:30px">What you just saw</div>
<div style="font:400 28px/1.7 sans-serif;color:#d6dee8">✓ One goal in plain language → the worker chose every step<br>
✓ Real browser on a vendor portal and an ERP<br>✓ HTTP 500 retried · renamed button adapted · crash reconciled<br>
✓ Human approval with a live re-check · injection-proof policy<br>✓ “Done” only after independent verification</div>""",
}


def cards() -> dict[str, Path]:
    out = {}
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        p = b.new_page(viewport={"width": SIZE[0], "height": SIZE[1]})
        for k, html in CARDS.items():
            p.set_content(CARD.format(html))
            out[k] = W / f"card-{k}.png"
            p.screenshot(path=out[k])
        b.close()
    return out


segs: list[tuple[str, Path, float, float, float, str]] = []  # kind, src, start, end, speed, narration


def clip(src: Path, a: float, b: float, speed: float = 1.0, text: str = "") -> None:
    if b - a > 0.3:
        segs.append(("clip", src, max(0.0, a), b, speed, text))


def card(img: Path, secs: float, text: str = "") -> None:
    segs.append(("card", img, 0, secs, 1.0, text))


def first(events: list[dict], kind: str, nth: int = 1, where=lambda p: True) -> float | None:
    hits = [e["t"] for e in events if e["type"] == kind and where(e["payload"])]
    return hits[nth - 1] if len(hits) >= nth else None


def run_scene(name: str, moments: list[tuple[float | None, str]], max_gap: float = 4.0) -> None:
    """Moments at 1x with narration (window grows to fit the narration); everything between is sped up."""
    src = D / f"{name}.webm"
    total = dur(src)
    pts = sorted((t, txt) for t, txt in moments if t is not None)
    cursor = 0.0
    for i, (t, txt) in enumerate(pts):
        start = max(cursor, t - 1.0)
        if start - cursor > 0.3:
            gap = start - cursor
            clip(src, cursor, start, max(1.0, gap / max_gap))
        _, alen = say_len(txt, 900 + len(segs))
        end = min(total, start + max(4.0, alen + 0.6))
        nxt = pts[i + 1][0] - 1.0 if i + 1 < len(pts) else total
        end = min(end, max(nxt, start + 2.0)) if alen == 0 else end
        clip(src, start, end, 1.0, txt)
        cursor = end
    if total - cursor > 0.3:
        clip(src, cursor, total, max(1.0, (total - cursor) / 10))


def build() -> None:
    c = cards()
    card(c["title"], 4.5, "This is CentrAlign: an autonomous worker. You give it a goal, and it figures out the work.")

    lm = dict((k, v) for k, v in TL["landing"]["marks"])
    land = D / "landing.webm"
    clip(land, 0.5, lm["how"], 1.0, "It turns a one-line request into finished, verified work in real company systems.")
    clip(land, lm["how"], lm["trust"], 1.0, "Each step is chosen by the model from the goal and the last observation, and "
                                            "every step passes the same pipeline: validate, policy, execute, observe, recover.")
    clip(land, lm["trust"], lm["arch"], 1.0, "The key rule: the model only proposes. Deterministic code decides what is "
                                             "allowed, so a manipulated model cannot move money.")
    clip(land, lm["arch"], dur(land), 1.0, "The worker drives a real browser through a mock vendor portal and an internal ERP.")

    e = TL["run_failures"]["events"]
    m = dict((k, v) for k, v in TL["run_failures"]["marks"])
    run_scene("run_failures", [
        (m["run_clicked"] - 3, "Here's the operator console. I load a plain-language goal: find the latest Acme invoice "
                               "and enter it into the ERP. And I switch on two failures in the ERP."),
        (first(e, "GOAL_UNDERSTOOD"), "The worker turns my sentence into a goal spec. From here, it decides every step."),
        (first(e, "INVOICE_SELECTED"), "It searched the portal in its browser, listed Acme's invoices, and chose "
                                       "INV ten forty-two: the newest by date. Not ten forty-five, which has a higher "
                                       "number but is older."),
        (first(e, "RECOVERY_ATTEMPTED", where=lambda p: p.get("strategy") == "adapt"),
         "The ERP's submit button was renamed. The worker sees the controls on the page, and proposes an adapted action. "
         "That proposal goes through validation and policy again."),
        (first(e, "RECOVERY_ATTEMPTED", where=lambda p: p.get("strategy") == "retry"),
         "Next, the ERP returns a server error. That's classified as transient, so the same action is retried, "
         "with the same idempotency key."),
        (first(e, "VERIFICATION_COMPLETED"), "The agent says it's done. That's only a claim. An independent verifier "
                                             "re-reads the portal and the ERP through their APIs, and compares every field."),
        (m["done"], "Verified, with the evidence and every check on screen."),
    ])

    agent_vids = sorted((D / "agent").glob("*.webm"), key=lambda p: p.stat().st_mtime)
    if agent_vids:
        v = agent_vids[0]
        card(c["browser"], 3.5, "This is the worker's own browser during that run.")
        L = dur(v)
        speed = max(1.0, L / 40)
        clip(v, 0, L, speed, "It searches the vendor portal, reads the invoice list, fills the ERP form, hits the "
                             "renamed button and the server error, and recovers, all through the same screens a "
                             "person would use.")

    e = TL["run_approval"]["events"]
    m = dict((k, v) for k, v in TL["run_approval"]["marks"])
    run_scene("run_approval", [
        (m["run_clicked"] - 2, "A different vendor, and a constraint: ask me before entering anything above fifty "
                               "thousand rupees."),
        (first(e, "INVOICE_SELECTED"), "The latest Gamma invoice is seventy-five thousand rupees."),
        (m.get("approve_click", 0) - 5, "So policy pauses the run and shows me the facts. The model can't approve "
                                        "anything itself, and a stored approval is tied to these exact values."),
        (first(e, "APPROVAL_REVALIDATION"), "After I approve, the worker re-reads the live facts before writing, so a "
                                            "stale approval can never be used."),
        (m["done"], "Verified, including a check that a human approval is on record."),
    ])

    e = TL["run_crash"]["events"]
    m = dict((k, v) for k, v in TL["run_crash"]["marks"])
    run_scene("run_crash", [
        (m["run_clicked"] - 2, "Last, the hard case: the worker will crash right after writing to the ERP, before "
                               "it records what happened."),
        (m.get("restart_click", 0) - 5, "The process died mid-action. When it restarts, it doesn't trust its memory."),
        (first(e, "ACTION_RECONCILED"), "It looks up its unrecorded write by idempotency key, finds it in the ERP, and "
                                        "does not write again."),
        (m["done"], "Verified: exactly one record, no duplicate."),
    ])

    clip(D / "erp.webm", 0.5, dur(D / "erp.webm"), 1.0, "And in the ERP itself: one record per invoice, entered "
                                                         "through its own web form.")
    card(c["close"], 9, "One goal in, the worker chose every step, recovered from failures, asked a human when policy "
                        "required, and proved the result. That's CentrAlign.")


def render() -> Path:
    parts = []
    w, h = SIZE
    vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=0x0b0e12,format=yuv420p"
    for n, (kind, src, a, b, speed, text) in enumerate(segs):
        v = W / f"v{n:03d}.mp4"
        if kind == "card":
            sh("ffmpeg", "-y", "-loop", "1", "-i", str(src), "-t", str(b), "-r", "30", "-vf", vf,
               "-c:v", "libx264", "-preset", "fast", str(v))
        else:
            sh("ffmpeg", "-y", "-ss", f"{a:.2f}", "-to", f"{b:.2f}", "-i", str(src), "-an", "-r", "30",
               "-vf", f"setpts=PTS/{speed:.3f},{vf}", "-c:v", "libx264", "-preset", "fast", str(v))
        vd = dur(v)
        audio, alen = say_len(text, n)
        total = max(vd, alen + 0.5 if audio else 0)
        out = W / f"p{n:03d}.mp4"
        ain = ["-i", str(audio)] if audio else ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
        sh("ffmpeg", "-y", "-i", str(v), *ain, "-filter_complex",
           f"[0:v]tpad=stop_mode=clone:stop_duration={total - vd + 0.1:.2f}[vv];[1:a]aresample=44100,apad[aa]",
           "-map", "[vv]", "-map", "[aa]", "-t", f"{total:.2f}", "-c:v", "libx264", "-preset", "fast",
           "-c:a", "aac", "-ar", "44100", "-ac", "2", str(out))
        parts.append(out)
        print(f"{n:02d} {kind} {speed:4.1f}x {total:5.1f}s {text[:60]}", flush=True)
    (W / "list.txt").write_text("".join(f"file '{p}'\n" for p in parts))
    final = D / "centralign-demo.mp4"
    sh("ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(W / "list.txt"), "-c:v", "libx264", "-preset", "medium",
       "-crf", "23", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(final))
    sh("ffmpeg", "-y", "-ss", "2", "-i", str(final), "-frames:v", "1", "-q:v", "3", str(D / "poster.jpg"))
    print(f"FINAL {final} {dur(final):.1f}s")
    return final


if __name__ == "__main__":
    build()
    render()
