"""The Files panel: upload a CSV, a cell that reads it, download it; and importing an .ipynb from
the home page."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright  # noqa: E402

from common import OUTPUT_DIR, code_text, fetch_text, finish, launch, log, new_notebook, new_page, open_home, outputs, run, shot, wait_for  # noqa: E402

errors: list[str] = []
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
csv = OUTPUT_DIR / "cities.csv"
csv.write_text("city,people\nTaipei,2.5\nKaohsiung,2.7\nTaichung,2.8\n")
notebook = OUTPUT_DIR / "imported.ipynb"
notebook.write_text(json.dumps({
    "nbformat": 4, "nbformat_minor": 5, "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"}},
    "cells": [
        {"id": "m1", "cell_type": "markdown", "metadata": {}, "source": ["# From Jupyter\n", "Imported."]},
        {"id": "c1", "cell_type": "code", "metadata": {}, "execution_count": 7, "source": ["print('kept output')"], "outputs": [{"output_type": "stream", "name": "stdout", "text": ["kept output\n"]}]},
    ],
}))

with sync_playwright() as pw:
    browser = launch(pw)
    page = new_page(browser, errors, "A")
    new_notebook(page, "Files")
    page.locator(".cl-cell.is-code .monaco-editor").first.click()
    page.click("#panel-files")
    page.set_input_files("#files_upload input[type=file]", str(csv))
    wait_for(lambda: "cities.csv" in page.locator("#files").inner_text(), "the file is listed")
    page.locator("#files button", has_text="Use").first.click()
    wait_for(lambda: page.locator(".cl-cell.is-code").count() == 2, "a cell reads it")
    assert "pd.read_csv(\"cities.csv\")" in code_text(page, 1)
    assert run(page, 1) == "cl-footer is-ok"
    assert "Kaohsiung" in outputs(page, 1)
    shot(page, "01_files")
    body = fetch_text(page, "/_cellora/files/cities.csv")
    assert body.startswith("city,people")
    log("files ok")
    open_home(page)
    page.set_input_files("#ipynb_upload input[type=file]", str(notebook))
    page.wait_for_url("**/nb/*", timeout=30000)
    page.wait_for_selector(".cl-cell .cl-stream")
    assert page.locator(".cl-cell").count() == 2 and "kept output" in outputs(page, 0)
    assert "From Jupyter" in page.locator("#nb-title").inner_text()
    log("import ok")
    browser.close()
finish(errors)
