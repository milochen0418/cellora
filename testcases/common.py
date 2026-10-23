"""Helpers shared by the Playwright suites (imported by each run_test.py)."""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import Browser, Page, Playwright

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:3000").rstrip("/")
# The Reflex backend: the same origin behind re-ddns, another port in a local run.
BACKEND_URL = os.environ.get("BACKEND_URL", BASE_URL).rstrip("/")
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", Path(__file__).resolve().parent / "output"))
HEADLESS = os.environ.get("HEADLESS", "1").strip().lower() not in {"0", "false", "no", "off"}
SLOWMO = int(os.environ.get("PW_SLOWMO_MS", "0") or 0)
TIMEOUT = int(os.environ.get("PW_TIMEOUT_MS", "30000") or 30000)
_T0 = time.time()


def log(*args) -> None:
    print(f"[{time.time() - _T0:6.2f}]", *args, flush=True)


def launch(pw: Playwright) -> Browser:
    args = []
    if BASE_URL.startswith("https://") and ".reflex-ddns.com" in BASE_URL:
        # A Mac may resolve *.reflex-ddns.com to the Docker-internal IP.
        args.append("--host-resolver-rules=MAP *.reflex-ddns.com 127.0.0.1")
    return pw.chromium.launch(headless=HEADLESS, slow_mo=SLOWMO, args=args)


def new_page(browser: Browser, errors: list[str], who: str, *, locale: str = "en-US", width: int = 1400, height: int = 900) -> Page:
    ctx = browser.new_context(viewport={"width": width, "height": height}, locale=locale, ignore_https_errors=True, accept_downloads=True)
    page = ctx.new_page()
    page.set_default_timeout(TIMEOUT)

    def on_console(msg) -> None:  # noqa: ANN001
        if msg.type == "error" and "Outdated Optimize Dep" not in msg.text and "Failed to load resource" not in msg.text:
            errors.append(f"[{who}] console: {msg.text}")

    def on_error(err) -> None:  # noqa: ANN001
        # Reflex's own window.onerror trips on error events without an error object (a Monaco
        # worker stopped while loading): not the page's fault.
        if "at window.onerror" in (getattr(err, "stack", "") or ""):
            return
        # Monaco rejects a promise when it cancels its own request (an inline suggestion
        # overtaken by more typing); the page marks it handled, Playwright still reports it.
        if str(err) == "Canceled":
            return
        errors.append(f"[{who}] pageerror: {err}" + (f"\n{getattr(err, 'stack', '')}" if os.environ.get("PW_STACKS") else ""))

    page.on("console", on_console)
    page.on("pageerror", on_error)
    return page


def shot(page: Page, name: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUTPUT_DIR / f"{name}.png"))


def wait_for(fn, what: str, timeout: float = 30.0) -> None:
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            if fn():
                return
        except Exception as e:  # noqa: BLE001 - keep trying until the deadline
            last = e
        time.sleep(0.2)
    raise AssertionError(f"timed out: {what}" + (f" (last error: {last})" if last else ""))


def open_home(page: Page) -> None:
    page.goto(BASE_URL + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#new-notebook", timeout=90000)


def new_notebook(page: Page, title: str = "", kind: str = "blank") -> str:
    """Makes a notebook from the home page ("blank" or "tour"); returns its id."""
    open_home(page)
    page.click("#new-notebook")
    if title:
        page.fill("#new-title", title)
    page.click("#create-tour" if kind == "tour" else "#create-blank")
    page.wait_for_url(re.compile(r".*/nb/[a-z0-9]+"), timeout=30000)
    wait_cells(page)
    return page.url.rstrip("/").rsplit("/", 1)[-1]


def open_notebook(page: Page, nb_id: str) -> None:
    page.goto(f"{BASE_URL}/nb/{nb_id}", wait_until="domcontentloaded")
    wait_cells(page)


def wait_cells(page: Page) -> None:
    page.wait_for_selector(".cl-cell", timeout=90000)
    page.wait_for_selector(".cl-cell .monaco-editor", timeout=90000)


_EDITOR_JS = """(i) => {
    const host = document.querySelectorAll('.cl-cell.is-code')[i]?.querySelector('.cl-editor-host .monaco-editor');
    if (!host || !window.monaco) return null;
    return monaco.editor.getEditors().find(e => host.contains(e.getContainerDomNode()) || e.getContainerDomNode().contains(host) || e.getContainerDomNode() === host) || null;
}"""


def code_text(page: Page, i: int = 0):
    """The text of the i-th code cell's editor (None while it has none)."""
    return page.evaluate(f"(i) => {{ const ed = ({_EDITOR_JS})(i); return ed ? ed.getValue() : null; }}", i)


def set_code(page: Page, i: int, code: str) -> None:
    """Puts code in the i-th code cell (as a paste would: typing meets the auto-closing quotes)."""
    wait_for(lambda: page.evaluate(f"(i) => !!({_EDITOR_JS})(i)", i), f"the editor of code cell {i}")
    page.evaluate(f"([i, code]) => {{ const ed = ({_EDITOR_JS})(i); ed.setValue(code); }}", [i, code])


def code_cell(page: Page, i: int = 0):
    return page.locator(".cl-cell.is-code").nth(i)


def state(page: Page, i: int = 0) -> str:
    footer = code_cell(page, i).locator(".cl-footer")
    return (footer.get_attribute("class") or "") if footer.count() else ""


def run(page: Page, i: int = 0, wait: bool = True, timeout: float = 90.0) -> str:
    """Runs the i-th code cell (⌘/Ctrl Enter in its editor); returns its state when done."""
    cell = code_cell(page, i)
    before = cell.locator(".cl-count").inner_text()
    cell.locator(".monaco-editor").click()
    page.keyboard.press("Meta+Enter" if sys.platform == "darwin" else "Control+Enter")
    wait_for(lambda: cell.locator(".cl-count").inner_text() != before or "is-queued" in state(page, i) or "is-running" in state(page, i), "the run started", 30)
    if wait:
        wait_for(lambda: any(s in state(page, i) for s in ("is-ok", "is-error", "is-cancelled")), "the run ended", timeout)
    return state(page, i)


def outputs(page: Page, i: int = 0) -> str:
    out = code_cell(page, i).locator(".cl-outputs")
    return out.inner_text() if out.count() else ""


def fetch_text(page: Page, path: str) -> str:
    """GET a path of the backend from the page (the browser trusts the re-ddns CA and resolves
    *.reflex-ddns.com; Python's urllib may do neither)."""
    return page.evaluate("async (url) => { const r = await fetch(url); if (!r.ok) throw new Error(r.status + ' ' + url); return await r.text(); }", BACKEND_URL + path)


def finish(errors: list[str]) -> None:
    if errors:
        print("\n".join(errors[:40]))
        raise SystemExit("page errors (see above)")
    log("PASS")
