"""Two people on one notebook: who is here, typing in the same cell at once, carets, outputs and
new cells reaching both, and someone leaving."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright  # noqa: E402

from common import code_cell, code_text, finish, launch, log, new_notebook, new_page, open_notebook, outputs, shot, wait_for  # noqa: E402

errors: list[str] = []
with sync_playwright() as pw:
    browser = launch(pw)
    a = new_page(browser, errors, "A", width=1300)
    b = new_page(browser, errors, "B", width=1300)
    nb = new_notebook(a, "Together")
    open_notebook(b, nb)
    wait_for(lambda: a.locator("#people-count").inner_text().startswith("2") and b.locator("#people-count").inner_text().startswith("2"), "both see two people")
    log("presence ok")
    a.locator(".cl-cell.is-code .monaco-editor").first.click()
    a.keyboard.type("x = 41", delay=20)
    wait_for(lambda: code_text(b, 0) == "x = 41", "B sees A typing")
    # Both type in the same cell at the same time.
    b.locator(".cl-cell.is-code .monaco-editor").first.click()
    b.keyboard.press("End")
    a.keyboard.press("Home")
    for ca, cb in zip("# hi\n", "\nprin"):
        a.keyboard.type(ca)
        b.keyboard.type(cb)
    b.keyboard.type("t(x + 1)")
    wait_for(lambda: code_text(a, 0) == code_text(b, 0) == "# hi\nx = 41\nprint(x + 1)", "the same merged text on both")
    log("merge ok")
    wait_for(lambda: a.locator(".yRemoteSelectionHead").count() > 0, "A sees B's caret")
    wait_for(lambda: a.locator(".cl-cell .cl-people .cl-dot").count() > 0, "A sees B in the cell")
    shot(a, "01_carets")
    # A runs it; B sees the output and who ran it.
    a.locator(".cl-cell.is-code .monaco-editor").first.click()
    a.keyboard.press("Shift+Enter")
    wait_for(lambda: "42" in outputs(b, 0), "B sees the output", 60)
    me_a = a.locator("#me-name").inner_text()
    wait_for(lambda: me_a in code_cell(b, 0).locator(".cl-footer").inner_text(), "B sees who ran it")
    wait_for(lambda: b.locator(".cl-cell").count() == 2, "B sees the cell Shift+Enter added")
    # B adds a text cell; A sees it.
    b.click("#add-text")
    # Typed only once the new cell has the keyboard (before that, letters are command keys).
    wait_for(lambda: b.locator(".cl-cell.is-md .monaco-editor").count() == 1, "B's new text cell is open")
    wait_for(lambda: b.evaluate("() => !!document.activeElement?.closest('.cl-cell.is-md')"), "B's text cell has the keyboard")
    b.keyboard.type("# Notes from B", delay=10)
    wait_for(lambda: a.locator(".cl-cell.is-md").count() == 1, "A sees B's text cell")
    b.keyboard.press("Escape")
    log("cells ok")
    # A follows B by clicking B's face.
    a.locator("#presence .cl-person").nth(1).click()
    # B closes the tab: A stops seeing B at once.
    t = time.time()
    b.close(run_before_unload=True)
    wait_for(lambda: a.locator("#people-count").inner_text().startswith("1"), "A sees B gone", 15)
    log(f"B gone after {time.time() - t:.1f}s")
    browser.close()
finish(errors)
