"""Call everyone (needs an installed call app, e.g. on re-ddns: avatar-chat, livekit or
video-chat): A calls everyone on the notebook, B gets the incoming call and accepts, both see
"Join call · 2", and the call ends when both hang up."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright  # noqa: E402

from common import BASE_URL, HEADLESS, SLOWMO, finish, log, new_notebook, new_page, open_notebook, shot, wait_for  # noqa: E402

errors: list[str] = []
with sync_playwright() as pw:
    args = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"]
    if BASE_URL.startswith("https://") and ".reflex-ddns.com" in BASE_URL:
        args.append("--host-resolver-rules=MAP *.reflex-ddns.com 127.0.0.1")
    browser = pw.chromium.launch(headless=HEADLESS, slow_mo=SLOWMO, args=args)
    a = new_page(browser, errors, "A")
    b = new_page(browser, errors, "B")
    for p in (a, b):
        p.context.grant_permissions(["camera", "microphone"])
    nb = new_notebook(a, "Call")
    open_notebook(b, nb)
    wait_for(lambda: a.locator("#people-count").inner_text().startswith("2"), "two people")
    if a.locator("#call-everyone").count() == 0:
        log("SKIP: calls need reflex_ddns_auth")
        raise SystemExit(0)
    a.click("#call-everyone")
    # With several call apps installed, the first caller picks one.
    a.wait_for_timeout(1500)
    chooser = a.locator("[role=dialog] button", has_text="Call")
    if a.locator("text=Choose").count() and chooser.count():
        chooser.first.click()
    wait_for(lambda: a.locator("iframe").count() > 0, "A's call dialog", 30)
    wait_for(lambda: b.locator("[aria-label='Incoming call']").count() == 1, "B is rung", 30)
    shot(b, "01_incoming")
    log("B rung by", b.locator("[aria-label='Incoming call']").inner_text().split("\n")[1])
    b.locator("[aria-label='Incoming call'] button[aria-label='Accept']").click()
    wait_for(lambda: b.locator("iframe").count() > 0, "B's call dialog", 30)
    wait_for(lambda: "2" in a.locator("#call-join").inner_text(), "Join call · 2 for A", 60)
    shot(a, "02_in_call")
    log("both in the call:", a.locator("#call-join").inner_text())
    browser.close()
finish([e for e in errors if "avatar" not in e.lower()])
