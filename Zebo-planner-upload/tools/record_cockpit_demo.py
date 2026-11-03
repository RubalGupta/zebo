"""Record the VP demo flow in the real FY27 cockpit (Streamlit) with Playwright — free, local, no API keys.

What it does
1. Starts the cockpit on http://localhost:8599 with a throw-away draft-plan folder (your own local_state/ is untouched).
2. Opens it in your installed Microsoft Edge (or Chrome) via Playwright and records a 1280x720 video.
3. Runs: Viewing as Gray Esterly (VP) -> Which areas are declining? -> Who owns the top declining? -> Show me his
   entire book -> What's the opportunity pipeline for it? -> Move Mardex Utilities to Dana Pemberton (+ apply with a
   reason) -> Is the new seller's territory pure? -> Should I implement the fix?
4. Saves Marketing/raw/cockpit_demo_raw.webm and Marketing/raw/cockpit_demo_marks.json (timings for editing).

Run it with record_demo.bat (Windows) or:  python tools/record_cockpit_demo.py
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "Marketing" / "raw"
PORT = 8599
URL = f"http://localhost:{PORT}"
W, H = 1280, 720

# (label, question, [(phrase to highlight in the answer, seconds to hold), ...])
FLOW = [
    ("declining", "Which areas are declining?", [("Greater China", 2.5), ("-8.5% YoY", 2.5)]),
    ("owner", "Who owns the top declining?", [("Taylor Marlowe", 2.0), ("-$3.7M", 1.8), ("89.8% of the revenue lost", 2.5)]),
    ("book", "Show me his entire book", [("8 accounts", 2.2), ("Over capacity:", 2.8), ("Motion misfit:", 2.8)]),
    ("pipeline", "What's the opportunity pipeline for it?", [("$26.1M", 2.4), ("$1.9M", 3.0)]),
    ("move", "Move Mardex Utilities to Dana Pemberton", [("Review with caution", 2.4), ("Capacity", 2.6)]),
    ("purity", "Is the new seller's territory pure? What should I do to fix it?",
     [("not pure", 2.4), ("Ortris Media", 2.2), ("How to fix it", 2.6), ("After the fix", 2.6)]),
    ("should", "Should I implement the fix?", [("My recommendation: yes", 2.6), ("It's your call.", 3.0)]),
]
HL_CSS = """
.nb-hl { background: rgba(255,138,31,.22); outline: 3px solid #ff8a1f; border-radius: 4px; padding: 0 2px;
         box-shadow: 0 0 0 6px rgba(255,138,31,.18); transition: all .3s; }
.nb-ring { position: fixed; z-index: 99999; pointer-events: none; border: 4px solid #ff8a1f; border-radius: 10px;
           box-shadow: 0 0 0 8px rgba(255,138,31,.22); }
"""
HL_JS = """([phrase, scope]) => {
  document.querySelectorAll('.nb-hl').forEach(e => e.classList.remove('nb-hl'));
  const msgs = [...document.querySelectorAll('[data-testid="stChatMessage"]')];
  const roots = scope === 'last' ? msgs.slice(-1) : msgs.slice(-3);
  for (const root of roots.reverse()) {
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = w.nextNode())) {
      const i = n.textContent.indexOf(phrase);
      if (i < 0) continue;
      const r = document.createRange(); r.setStart(n, i); r.setEnd(n, i + phrase.length);
      const span = document.createElement('span'); span.className = 'nb-hl';
      r.surroundContents(span);
      span.scrollIntoView({block: 'center', behavior: 'smooth'});
      return true;
    }
  }
  return false;
}"""
RING_JS = """(sel) => {
  document.querySelectorAll('.nb-ring').forEach(e => e.remove());
  if (!sel) return;
  const e = document.querySelector(sel); if (!e) return;
  const r = e.getBoundingClientRect(); const d = document.createElement('div'); d.className = 'nb-ring';
  Object.assign(d.style, {left: (r.left - 6) + 'px', top: (r.top - 6) + 'px', width: (r.width + 12) + 'px', height: (r.height + 12) + 'px'});
  document.body.appendChild(d);
}"""
REASON = "Expansion account belongs with an Expansion AE; Taylor is over capacity"


def wait_port(port: int, timeout: float = 90) -> None:
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.5)
    raise SystemExit(f"The app did not start on port {port} within {timeout:.0f}s")


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for st in self.streams:
            try:
                st.write(data)
                st.flush()
            except Exception:
                pass

    def flush(self):
        pass


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    out_log = open(OUT / "recorder_output.txt", "w", encoding="utf-8")
    sys.stdout = _Tee(sys.__stdout__, out_log)
    sys.stderr = _Tee(sys.__stderr__, out_log)
    try:
        _main()
    except BaseException:
        traceback.print_exc()
        raise


def _main() -> None:
    from playwright.sync_api import sync_playwright

    state = Path(tempfile.mkdtemp(prefix="norbae_demo_state_"))
    env = dict(os.environ, NORBAE_STATE_DIR=str(state), NORBAE_ADVISOR_ENGINE="rules")
    log = open(OUT / "record_log.txt", "w", encoding="utf-8")
    app = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "streamlit_app.py", "--server.port", str(PORT),
                            "--server.headless", "true", "--browser.gatherUsageStats", "false"],
                           cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    marks: list[dict] = []
    try:
        wait_port(PORT)
        vid_dir = Path(tempfile.mkdtemp(prefix="norbae_vid_"))
        with sync_playwright() as p:
            browser = None
            for channel in ("msedge", "chrome", None):
                try:
                    browser = p.chromium.launch(channel=channel, headless=False) if channel else p.chromium.launch(headless=False)
                    print("Browser:", channel or "playwright chromium")
                    break
                except Exception as exc:  # channel not installed -> try the next one
                    print("Browser", channel, "not available:", str(exc).splitlines()[0])
            if browser is None:
                raise SystemExit("No browser available (install Microsoft Edge or Google Chrome)")
            ctx = browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(vid_dir),
                                      record_video_size={"width": W, "height": H})
            t0 = time.time()
            mark = lambda label, **kw: marks.append({"label": label, "t": round(time.time() - t0, 2), **kw})
            page = ctx.new_page()
            try:
                run_flow(page, mark)
            except Exception:
                err = traceback.format_exc()
                print(err)
                (OUT / "record_error.txt").write_text(err, encoding="utf-8")
                try:
                    page.screenshot(path=str(OUT / "record_error.png"))
                    (OUT / "record_error_page.html").write_text(page.content(), encoding="utf-8")
                except Exception:
                    pass
            video = page.video
            ctx.close()
            browser.close()
            raw = Path(video.path())
            dest = OUT / "cockpit_demo_raw.webm"
            shutil.copyfile(raw, dest)
        (OUT / "cockpit_demo_marks.json").write_text(json.dumps(marks, indent=1), encoding="utf-8")
        print("Saved", dest)
        print("Saved", OUT / "cockpit_demo_marks.json")
    finally:
        app.terminate()
        try:
            app.wait(10)
        except Exception:
            app.kill()
        log.close()
        shutil.rmtree(state, ignore_errors=True)


def run_flow(page, mark) -> None:
    page.goto(URL)
    page.get_by_text("Advisor brain").first.wait_for(timeout=90000)
    page.add_style_tag(content=HL_CSS)
    page.wait_for_timeout(2500)
    mark("loaded")

    def css():   # Streamlit can re-render <head>; re-add our style if needed
        if not page.evaluate("() => !!document.querySelector('style[data-nb]')"):
            page.evaluate("(c) => { const s = document.createElement('style'); s.dataset.nb = 1; s.textContent = c; "
                          "document.head.appendChild(s); }", HL_CSS)

    # 1. Viewing as Gray Esterly (VP) — ring the selector, pick the VP
    css()
    sel = '[data-testid="stSidebar"] [data-testid="stSelectbox"]'
    page.evaluate(RING_JS, sel)
    mark("persona_ring")
    page.wait_for_timeout(2000)
    page.locator(sel).first.click()
    page.wait_for_timeout(1200)
    page.get_by_role("option", name="Gray Esterly", exact=False).first.click()
    page.wait_for_timeout(1800)
    css()
    page.evaluate(RING_JS, sel)
    page.wait_for_timeout(2500)
    page.evaluate(RING_JS, None)
    mark("persona")

    def advisor_input():
        box = page.get_by_placeholder("Ask about your business", exact=False).first
        if not box.is_visible():
            page.locator(".st-key-advisor_fab button").first.click()
            box.wait_for(timeout=15000)
            page.wait_for_timeout(800)
        return box

    def highlight(label, phrase, hold, scope="last"):
        css()
        found = page.evaluate(HL_JS, [phrase, scope])
        mark(f"hl_{label}", phrase=phrase, found=bool(found))
        page.wait_for_timeout(int(hold * 1000))

    def ask(label: str, q: str, hls) -> None:
        box = advisor_input()
        n = page.locator('[data-testid="stChatMessage"]').count()
        box.click()
        mark(label + "_type", question=q)
        box.type(q, delay=45)
        page.wait_for_timeout(500)
        page.get_by_role("button", name="Send").first.click()
        mark(label + "_sent")
        page.wait_for_function("(n) => document.querySelectorAll('[data-testid=stChatMessage]').length >= n", arg=n + 2,
                               timeout=30000)
        page.wait_for_timeout(900)
        page.evaluate("""() => { const m = [...document.querySelectorAll('[data-testid="stChatMessage"]')];
                                 if (m.length) m[m.length - 1].scrollIntoView({block: 'start', behavior: 'smooth'}); }""")
        page.wait_for_timeout(1200)
        mark(label + "_answered")
        for phrase, hold in hls:
            highlight(label, phrase, hold)
        mark(label + "_end")

    for label, q, hls in FLOW:
        ask(label, q, hls)
        if label == "move":
            reason = page.get_by_label("Your reason (required to apply)").last
            reason.scroll_into_view_if_needed()
            page.wait_for_timeout(600)
            reason.click()
            mark("reason_type")
            reason.type(REASON, delay=30)
            reason.press("Enter")
            page.wait_for_timeout(1500)
            btn = page.get_by_role("button", name="Apply to draft plan").last
            btn.scroll_into_view_if_needed()
            css()
            page.evaluate("(el) => el.classList.add('nb-hl')", btn.element_handle())
            page.wait_for_timeout(1500)
            mark("apply_click")
            btn.click()
            page.wait_for_timeout(3500)
            mark("applied")
    page.wait_for_timeout(2000)
    mark("flow_end")
    try:
        product_tour(page, mark)
    except Exception:
        traceback.print_exc()          # the tour is optional; the Advisor flow above is already recorded
    mark("end")


def product_tour(page, mark) -> None:
    """Close the Advisor and show the main screens (for marketing cut-aways)."""
    box = page.get_by_placeholder("Ask about your business", exact=False).first
    if box.is_visible():
        page.locator(".st-key-advisor_fab button").first.click()
        page.wait_for_timeout(1200)
    side = page.locator('[data-testid="stSidebar"]')

    def scroll(px, steps=12, pause=90):
        page.mouse.move(700, 450)
        for _ in range(steps):
            page.mouse.wheel(0, px / steps)
            page.wait_for_timeout(pause)

    for key, label, down in [("business", "1 · My business", 520), ("accounts", "2 · Accounts & owners", 420),
                             ("moves", "3 · Plan moves", 620), ("purity", "4 · Territory purity", 420)]:
        side.get_by_text(label, exact=True).first.click()
        page.wait_for_timeout(2600)
        page.mouse.move(700, 450)
        page.mouse.wheel(0, -3000)
        page.wait_for_timeout(600)
        mark(f"tour_{key}")
        page.wait_for_timeout(2600)
        mark(f"tour_{key}_scroll")
        scroll(down)
        page.wait_for_timeout(2200)
        mark(f"tour_{key}_end")


if __name__ == "__main__":
    main()
