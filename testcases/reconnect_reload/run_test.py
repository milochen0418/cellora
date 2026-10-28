"""A tab that stays open while the server reads the notebook from its file again (here the
notebook closed as idle while the tab was cut off; a restart of the server does the same) takes
the server's document instead of merging its own copy into it: no cell is doubled, what the tab
typed meanwhile comes along, and the others see it.

Needs the server started with a short CELLORA_NOTEBOOK_IDLE_MINUTES (0.05); skipped otherwise."""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright  # noqa: E402

from common import code_text, finish, launch, log, new_notebook, new_page, open_notebook, set_code, shot, wait_for  # noqa: E402

IDLE_MINUTES = float(os.environ.get("CELLORA_NOTEBOOK_IDLE_MINUTES") or 0)
if not IDLE_MINUTES or IDLE_MINUTES > 0.5:
    log("SKIP: start the server with CELLORA_NOTEBOOK_IDLE_MINUTES=0.05 (the notebook must close as idle within the test)")
    sys.exit(0)
JANITOR_EVERY = 20

# The notebook's sockets can be cut and held off from the test (the Reflex socket stays): closing
# one from the browser is what a dropped connection comes down to, and a refused port stands in
# for the server being away.
WS_HOOK = """(() => {
  const Real = window.WebSocket;
  window.__ws = { open: [], away: false };
  function Hooked(url, protocols) {
    const yjs = String(url).includes("/_cellora/yjs/");
    const target = yjs && window.__ws.away ? "ws://127.0.0.1:1/" : url;
    const ws = protocols === undefined ? new Real(target) : new Real(target, protocols);
    if (yjs) window.__ws.open.push(ws);
    return ws;
  }
  Hooked.prototype = Real.prototype;
  Object.keys(Real).forEach((k) => { Hooked[k] = Real[k]; });
  window.WebSocket = Hooked;
})();"""


def generation(page) -> str:
    return page.locator(".cl-notebook").get_attribute("data-generation") or ""


errors: list[str] = []
with sync_playwright() as pw:
    browser = launch(pw)
    a = new_page(browser, errors, "A", width=1300)
    a.add_init_script(WS_HOOK)
    nb = new_notebook(a, "Reload")
    set_code(a, 0, "x = 1")
    wait_for(lambda: generation(a) == "0", "the first document")
    time.sleep(3)  # saved (2 s after a change)
    # The connection drops (as when a laptop sleeps); the tab goes on typing.
    a.evaluate("() => { window.__ws.away = true; window.__ws.open.filter((w) => w.readyState === 1).forEach((w) => w.close()); }")
    wait_for(lambda: a.locator(".cl-offline").count() == 1, "A sees the connection lost", 20)
    a.locator(".cl-cell.is-code .monaco-editor").first.click()
    a.keyboard.press("End")
    a.keyboard.type("\ny = 2", delay=20)
    a.keyboard.press("Escape")
    a.keyboard.press("b")
    wait_for(lambda: a.locator(".cl-cell").count() == 2, "the cell added while cut off")
    wait_for(lambda: a.evaluate("() => { const c = document.querySelectorAll('.cl-cell'); return !!c[1] && c[1].contains(document.activeElement); }"), "the new cell has the keyboard")
    a.keyboard.type("z = 3", delay=20)
    log("typed while cut off")
    # Meanwhile the server saves and closes the notebook nobody has open; when A is back, it
    # reads the notebook from its file again: a new document with the same cells.
    time.sleep(IDLE_MINUTES * 60 + JANITOR_EVERY + 5)
    a.evaluate("() => { window.__ws.away = false; }")
    wait_for(lambda: generation(a) == "1", "A took the document again", 30)
    wait_for(lambda: a.locator(".cl-offline").count() == 0, "A is back")
    assert a.locator(".cl-cell").count() == 2, f"cells doubled: {a.locator('.cl-cell').count()}"
    wait_for(lambda: code_text(a, 0) == "x = 1\ny = 2" and code_text(a, 1) == "z = 3", "what A typed while cut off came along")
    shot(a, "01_after_reload")
    log("reloaded, nothing doubled, typing kept")
    # The server has it too, and the notebook still works with others after the reload.
    b = new_page(browser, errors, "B", width=1300)
    open_notebook(b, nb)
    wait_for(lambda: b.locator(".cl-cell").count() == 2 and code_text(b, 0) == "x = 1\ny = 2" and code_text(b, 1) == "z = 3", "B sees the same")
    wait_for(lambda: a.locator("#people-count").inner_text().startswith("2") and b.locator("#people-count").inner_text().startswith("2"), "both see two people")
    b.locator(".cl-cell.is-code .monaco-editor").nth(1).click()
    b.keyboard.press("End")
    b.keyboard.type("  # B", delay=20)
    wait_for(lambda: code_text(a, 1) == "z = 3  # B", "A sees B typing after the reload")
    a.locator(".cl-cell.is-code .monaco-editor").first.click()
    a.keyboard.press("End")
    a.keyboard.type("  # A", delay=20)
    wait_for(lambda: code_text(b, 0) == "x = 1\ny = 2  # A", "B sees A typing after the reload")
    shot(a, "02_together")
    browser.close()
# The held-off reconnections are refused by design.
errors[:] = [e for e in errors if "ws://127.0.0.1:1/" not in e]
finish(errors)
