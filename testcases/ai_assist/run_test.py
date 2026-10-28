"""AI help (needs an AI: Claude with ANTHROPIC_API_KEY, or Ollama with a model): the grey
suggestion and Tab, ⌘I with a diff to accept, Fix with AI, the assistant panel's answers and
"Insert"."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright  # noqa: E402

from common import code_cell, code_text, finish, launch, log, new_notebook, new_page, outputs, run, set_code, shot, state, wait_for  # noqa: E402

errors: list[str] = []
MOD = "Meta" if sys.platform == "darwin" else "Control"
with sync_playwright() as pw:
    browser = launch(pw)
    page = new_page(browser, errors, "A")
    new_notebook(page, "AI")
    page.click("#ai-toggle")
    page.wait_for_selector("#ai-panel")
    if page.locator("#ai-panel", has_text="Not set up").count():
        log("SKIP: no AI here (set ANTHROPIC_API_KEY, or run Ollama with a model)")
        browser.close()
        raise SystemExit(0)
    # The grey suggestion while typing; Tab takes it.
    page.locator(".cl-cell.is-code .monaco-editor").first.click()
    page.keyboard.type("def add(a, b):", delay=20)
    page.keyboard.press("Enter")
    wait_for(lambda: page.locator(".ghost-text-decoration, .ghost-text").count() > 0, "a grey suggestion", 40)
    page.keyboard.press("Tab")
    wait_for(lambda: len(code_text(page, 0)) > len("def add(a, b):\n    "), "Tab took it")
    log("inline ok:", repr(code_text(page, 0)[:80]))
    # ⌘I: write the cell from an instruction; accept the diff.
    set_code(page, 0, "")
    page.locator(".cl-cell.is-code .monaco-editor").first.click()
    page.keyboard.press(f"{MOD}+i")
    page.wait_for_selector(".cl-ai-prompt input")
    page.keyboard.type("print the squares of 1 to 5, one per line", delay=5)
    page.keyboard.press("Enter")
    page.wait_for_selector(".cl-draft")
    wait_for(lambda: page.locator(".cl-draft .cl-btn-primary").is_enabled() and page.locator(".cl-draft .cl-draft-status").count() == 0, "the draft", 120)
    shot(page, "01_draft")
    page.keyboard.press(f"{MOD}+Enter")
    wait_for(lambda: page.locator(".cl-draft").count() == 0, "accepted")
    assert run(page, 0) == "cl-footer is-ok"
    assert "25" in outputs(page, 0)
    log("⌘I ok")
    # Fix with AI.
    set_code(page, 0, "nums = [1, 2, 3]\nprint(sum(nums) / len(nmus))")
    assert "is-error" in run(page, 0)
    code_cell(page, 0).locator(".cl-error button", has_text="Fix with AI").click()
    wait_for(lambda: page.locator(".cl-draft .cl-btn-primary").is_enabled() and page.locator(".cl-draft .cl-draft-status").count() == 0, "the fix", 120)
    page.locator(".cl-draft .cl-btn-primary").click()
    wait_for(lambda: page.locator(".cl-draft").count() == 0, "the fix accepted")
    assert "nmus" not in code_text(page, 0)
    assert run(page, 0) == "cl-footer is-ok" and "2.0" in outputs(page, 0)
    log("fix ok")
    # The assistant panel answers from the notebook and inserts its code.
    page.fill("#ai-input", "Give me one code cell that prints the type of nums.")
    page.click("#ai-send")
    wait_for(lambda: page.input_value("#ai-input") == "", "the box emptied on send")
    wait_for(lambda: page.locator("#ai-messages .cl-spinner").count() == 0 and page.locator("#ai-messages .cl-chat-code").count() > 0, "an answer with code", 120)
    shot(page, "02_chat")
    page.locator("#ai-messages .cl-chat-code button", has_text="Insert").first.click()
    wait_for(lambda: page.locator(".cl-cell.is-code").count() == 2, "inserted as a new cell")
    log("chat ok")
    browser.close()
finish(errors)
