"""With CELLORA_RUN_PASSCODE set, running code asks for it (reading and editing don't)."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright  # noqa: E402

from common import finish, launch, log, new_notebook, new_page, outputs, set_code, state, wait_for  # noqa: E402

PASS = os.environ.get("CELLORA_RUN_PASSCODE", "")
if not PASS:
    log("SKIP: run with CELLORA_RUN_PASSCODE=<something>")
    raise SystemExit(0)

errors: list[str] = []
with sync_playwright() as pw:
    browser = launch(pw)
    page = new_page(browser, errors, "A")
    new_notebook(page, "Locked")
    set_code(page, 0, "print('unlocked')")
    page.locator(".cl-cell.is-code .cl-run").first.click()
    page.wait_for_selector("#passcode-input")
    page.fill("#passcode-input", "wrong")
    page.click("#passcode-ok")
    wait_for(lambda: page.locator("text=That passcode is not right.").count() == 1, "refused")
    page.fill("#passcode-input", PASS)
    page.click("#passcode-ok")
    wait_for(lambda: "is-ok" in state(page, 0), "ran after unlocking", 60)
    assert "unlocked" in outputs(page, 0)
    log("passcode ok")
    browser.close()
finish(errors)
