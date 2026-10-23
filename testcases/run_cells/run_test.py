"""Running cells: Run all stops at an error, Stop, a crash and a restart, huge and rewritten
output, rich outputs, and the variables."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright  # noqa: E402

from common import code_cell, finish, launch, log, new_notebook, new_page, outputs, run, set_code, shot, state, wait_for  # noqa: E402

errors: list[str] = []
with sync_playwright() as pw:
    browser = launch(pw)
    page = new_page(browser, errors, "A")
    new_notebook(page, "Runs")
    # Three cells: the middle one fails, so Run all cancels the last.
    set_code(page, 0, "a = 1")
    page.locator(".cl-cell.is-code").first.click()
    page.keyboard.press("Escape")
    page.keyboard.press("b")
    wait_for(lambda: page.locator(".cl-cell.is-code").count() == 2, "second cell")
    page.keyboard.press("Escape")
    page.keyboard.press("b")
    wait_for(lambda: page.locator(".cl-cell.is-code").count() == 3, "third cell")
    set_code(page, 1, "raise ValueError('on purpose')")
    set_code(page, 2, "print('after')")
    page.click("#run-all")
    wait_for(lambda: "is-cancelled" in state(page, 2) and "is-error" in state(page, 1), "run all stopped at the error", 60)
    assert "is-ok" in state(page, 0) and "ValueError" in outputs(page, 1) and outputs(page, 2) == ""
    log("run all ok")
    # Rich outputs: HTML, Markdown, LaTeX, an image, JSON.
    set_code(page, 2, "from IPython.display import HTML, Markdown, Math, display\ndisplay(HTML('<b id=\"bold\">bold</b>'))\ndisplay(Markdown('**md**'))\ndisplay(Math(r'e^{i\\pi}+1=0'))\n{'k': [1, 2]}")
    assert run(page, 2) == "cl-footer is-ok"
    cell = code_cell(page, 2)
    assert cell.locator(".cl-html b").count() == 1 and cell.locator(".cl-md strong").count() == 1 and cell.locator(".katex").count() >= 1
    log("rich outputs ok")
    # A progress bar rewrites its line; a flood of printing is cut at 1 MB.
    set_code(page, 0, "import time\nfor i in range(20):\n    print(f'\\rstep {i+1}/20', end='')\n    time.sleep(0.02)\nprint()\nprint('done')")
    assert run(page, 0) == "cl-footer is-ok"
    assert outputs(page, 0).strip() == "step 20/20\ndone"
    set_code(page, 0, "for i in range(300000):\n    print(i)")
    t = time.time()
    assert run(page, 0, timeout=120) == "cl-footer is-ok"
    assert "output cut" in outputs(page, 0)
    log(f"flood ok ({time.time() - t:.1f}s)")
    # Stop.
    set_code(page, 0, "import time\ntime.sleep(60)")
    run(page, 0, wait=False)
    wait_for(lambda: "is-running" in state(page, 0), "running", 30)
    page.click("#interrupt")
    wait_for(lambda: "is-error" in state(page, 0), "stopped", 30)
    assert "KeyboardInterrupt" in outputs(page, 0)
    # A crash, then a restart.
    set_code(page, 0, "import os\nos._exit(1)")
    run(page, 0)
    wait_for(lambda: "Stopped" in page.locator("#kernel-state").inner_text(), "kernel stopped", 30)
    assert "KernelStopped" in outputs(page, 0)
    page.click("#restart-kernel")
    page.click("#restart-confirm")
    wait_for(lambda: page.locator("#kernel-state").inner_text() == "Idle", "restarted", 60)
    log("stop, crash, restart ok")
    # Variables.
    set_code(page, 0, "import pandas as pd\nframe = pd.DataFrame({'city': ['Taipei', 'Tainan'], 'n': [3, 4]})\ncount = 7")
    assert run(page, 0) == "cl-footer is-ok"
    page.click("#panel-variables")
    wait_for(lambda: "frame" in page.locator("#variables").inner_text() and "count" in page.locator("#variables").inner_text(), "variables listed")
    assert "2 × 2" in page.locator("#variables").inner_text()
    shot(page, "01_variables")
    log("variables ok")
    browser.close()
finish(errors)
