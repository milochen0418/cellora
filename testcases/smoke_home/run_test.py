"""The home page, the getting-started tour, running a cell, the table and chart, the outline,
downloads, the language switch."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright  # noqa: E402

from common import code_cell, fetch_text, finish, launch, log, new_notebook, new_page, outputs, run, shot, wait_for  # noqa: E402

errors: list[str] = []
with sync_playwright() as pw:
    browser = launch(pw)
    page = new_page(browser, errors, "A")
    nb = new_notebook(page, kind="tour")
    log("tour notebook", nb)
    assert page.locator(".cl-cell").count() == 9
    assert page.locator(".cl-outline-item").count() >= 5, "outline lists the headings"
    shot(page, "01_tour")
    assert run(page, 0) == "cl-footer is-ok"
    page.wait_for_selector(".cl-table-box")
    table = page.locator(".cl-table-box").first
    assert "12 of 12 rows" in table.inner_text()
    # Sort by a column, filter, then the chart.
    table.locator("th", has_text="revenue").click()
    table.locator(".cl-table-search").fill("West")
    wait_for(lambda: "of " in table.locator(".cl-table-count").inner_text(), "filtered count")
    table.locator(".cl-tabs button", has_text="Chart").click()
    page.wait_for_selector(".cl-chart-canvas canvas")
    shot(page, "02_chart")
    log("table + chart ok")
    # SQL on the DataFrame.
    assert run(page, 1) == "cl-footer is-ok"
    assert "4 of 4 rows" in code_cell(page, 1).locator(".cl-table-box").inner_text()
    # A plot.
    assert run(page, 2) == "cl-footer is-ok"
    page.wait_for_selector(".cl-image")
    # The deliberate mistake.
    assert "is-error" in run(page, 3)
    assert "KeyError" in outputs(page, 3)
    log("sql, plot, error ok")
    # The file, as Jupyter reads it.
    data = json.loads(fetch_text(page, f"/_cellora/download/{nb}.ipynb"))
    assert data["nbformat"] == 4 and len(data["cells"]) == 9
    assert "application/vnd.cellora.table+json" in data["cells"][1]["outputs"][0]["data"]
    script = fetch_text(page, f"/_cellora/download/{nb}.py")
    assert "# %%" in script and "%%sql" in script
    log("downloads ok")
    # 中文.
    page.click("#lang-toggle")
    wait_for(lambda: "全部執行" in page.locator("#run-all").get_attribute("title"), "toolbar in Chinese")
    shot(page, "03_zh")
    page.click("#lang-toggle")
    browser.close()
finish(errors)
