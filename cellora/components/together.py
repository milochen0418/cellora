"""What shows that a notebook is used together (as in ReflexGrid's title bar): who is here, the
link to share, the account, Call everyone, an incoming call, and the page's heartbeat and
goodbye."""

from __future__ import annotations

import reflex as rx

from cellora.api import BYE_PATH
from cellora.call import CALLS_ENABLED, CallState
from cellora.states.lang import LangState
from cellora.states.notebook import AuthState, NotebookState
from cellora.states.room import NotebookRoom

T = LangState.t
ME = NotebookState.me["peer"]


def person(p) -> rx.Component:
    """Someone on the notebook, ringed in their colour; a click goes to the cell they are in."""
    face = rx.cond(
        p["avatar"] != "",
        rx.image(src=p["avatar"], alt=p["name"], class_name="h-7 w-7 rounded-full object-cover"),
        rx.el.span(
            p["initials"],
            class_name="h-7 w-7 rounded-full flex items-center justify-center text-[10px] font-bold text-white",
            style={"backgroundColor": p["color"]},
        ),
    )
    return rx.el.button(
        face,
        on_click=NotebookState.follow(p["id"]),
        type="button",
        title=p["name"],
        custom_attrs={"data-name": p["name"]},
        class_name="cl-person -ml-1.5 first:ml-0 rounded-full border-2 bg-white hover:z-10 hover:scale-110 transition-transform",
        style={"borderColor": p["color"]},
    )


def presence() -> rx.Component:
    return rx.el.div(
        rx.el.div(rx.foreach(NotebookRoom.people, person), class_name="flex items-center pl-1.5"),
        rx.el.span(
            NotebookRoom.people_count.to(str) + " " + T["here"],
            id="people-count",
            class_name="hidden sm:inline text-xs text-slate-500 whitespace-nowrap",
        ),
        id="presence",
        class_name="flex items-center gap-2 min-w-0",
    )


def share_button() -> rx.Component:
    return rx.el.button(
        rx.icon("link", class_name="h-4 w-4"),
        rx.el.span(T["share"], class_name="hidden md:inline"),
        on_click=[
            rx.call_script("navigator.clipboard && navigator.clipboard.writeText(window.location.href)"),
            rx.toast.success(T["link_copied"], position="bottom-center"),
        ],
        id="share-link",
        title=T["share_title"],
        class_name="cl-top-btn",
    )


def identity() -> rx.Component:
    """Who you are here, and signing in or out (with reflex_ddns_auth)."""
    link = "text-xs whitespace-nowrap hover:underline"
    name = rx.el.span(
        NotebookState.me["name"],
        id="me-name",
        class_name="text-xs font-medium text-slate-700 whitespace-nowrap max-w-[8rem] truncate",
        style={"borderBottom": "2px solid " + NotebookState.me["color"].to(str)},
    )
    if AuthState is None:
        return rx.el.div(name, class_name="flex items-center gap-2")
    return rx.el.div(
        name,
        rx.cond(
            AuthState.is_logged_in,
            rx.el.a(T["sign_out"], href=AuthState.logout_url, class_name=f"{link} text-slate-400"),
            rx.el.a(T["sign_in"], href=AuthState.login_url, class_name=f"{link} text-indigo-600"),
        ),
        class_name="hidden sm:flex items-center gap-2",
    )


def lang_toggle(then: list | None = None) -> rx.Component:
    return rx.el.button(
        rx.cond(LangState.lang == "en", "中文", "EN"),
        on_click=[LangState.pick(rx.cond(LangState.lang == "en", "zh-TW", "en")), *(then or [])],
        type="button",
        id="lang-toggle",
        title=T["language"],
        class_name="px-2 py-1 rounded-md text-xs font-medium text-slate-500 hover:bg-slate-100 whitespace-nowrap shrink-0",
    )


def call_button() -> rx.Component:
    """Call everyone on the notebook, or join its call while one is in progress."""
    if not CALLS_ENABLED:
        return rx.fragment()
    return rx.cond(
        NotebookRoom.call_count > 0,
        rx.el.button(
            rx.icon("phone-call", class_name="h-4 w-4"),
            rx.el.span(T["join_call"], " · ", NotebookRoom.call_count, class_name="whitespace-nowrap"),
            on_click=CallState.join_call,
            id="call-join",
            title=T["join_call_title"],
            class_name="cl-top-btn !bg-emerald-600 !text-white !border-emerald-600 hover:!bg-emerald-500",
        ),
        rx.el.button(
            rx.icon("phone", class_name="h-4 w-4"),
            rx.el.span(T["call_everyone"], class_name="hidden lg:inline whitespace-nowrap"),
            on_click=CallState.join_call,
            id="call-everyone",
            title=T["call_everyone_title"],
            aria_label="Call everyone",
            class_name="cl-top-btn",
        ),
    )


def incoming_call() -> rx.Component:
    """Someone called everyone on the notebook: accept or decline."""
    if not CALLS_ENABLED:
        return rx.fragment()
    me = NotebookState.me["user"]
    rung = (
        (NotebookRoom.call_ringing_from != "")
        & NotebookRoom.call_invited.contains(me)
        & ~NotebookRoom.call_responded.contains(me)
        & (NotebookState.nb_id != "")
    )
    round_button = "p-2.5 rounded-full flex cursor-pointer border-0"
    # Above the intent dialog (z-index 1000), so a call can be answered from inside another.
    return rx.cond(
        rung,
        rx.el.div(
            rx.el.div(
                rx.icon("phone-incoming", class_name="h-5 w-5 text-white"),
                class_name="h-10 w-10 rounded-full bg-emerald-500 flex items-center justify-center shrink-0 animate-pulse",
            ),
            rx.el.div(
                rx.el.span(T["incoming_call"], class_name="text-[11px] font-semibold uppercase tracking-wider text-emerald-600"),
                rx.el.span(NotebookRoom.call_ringing_from, class_name="text-sm font-bold text-slate-900 truncate"),
                rx.el.span(T["is_calling_everyone"], class_name="text-xs text-slate-500"),
                class_name="flex flex-col min-w-0 flex-1",
            ),
            rx.el.button(
                rx.icon("phone-off", class_name="h-4 w-4"),
                on_click=CallState.decline_call,
                title=T["decline"],
                aria_label="Decline",
                class_name=f"{round_button} bg-red-50 text-red-600 hover:bg-red-100",
            ),
            rx.el.button(
                rx.icon("phone", class_name="h-4 w-4"),
                on_click=CallState.join_call,
                title=T["accept"],
                aria_label="Accept",
                class_name=f"{round_button} bg-emerald-600 text-white hover:bg-emerald-500",
            ),
            role="alertdialog",
            aria_label="Incoming call",
            class_name="fixed bottom-6 right-4 sm:right-6 z-[1001] w-[min(21rem,calc(100vw-2rem))] flex items-center gap-3 p-3 rounded-2xl bg-white border border-slate-200 shadow-2xl",
        ),
    )


_CALL_HEARTBEAT_JS = """
if (window.__clCallHb) clearInterval(window.__clCallHb);
window.__clCallHb = setInterval(function () {
    var el = document.getElementById('call-heartbeat-trigger');
    if (!el) { clearInterval(window.__clCallHb); window.__clCallHb = null; return; }
    el.click();
}, 3000);
"""


@rx.memo
def call_keepalive() -> rx.Component:
    """While in a call, keep its heartbeat going on every page (the call runs in the app-wide
    intent dialogs, so it outlives the notebook page)."""
    return rx.cond(
        CallState.current_notebook != "",
        rx.el.button(
            id="call-heartbeat-trigger",
            on_click=CallState.heartbeat,
            on_mount=rx.call_script(_CALL_HEARTBEAT_JS),
            tab_index=-1,
            aria_hidden="true",
            class_name="sr-only",
        ),
    )


_HOOKS_JS = f"""
if (window.__clPing) clearInterval(window.__clPing);
window.__clPing = setInterval(function () {{
    var el = document.getElementById('cl-ping');
    if (!el) {{ clearInterval(window.__clPing); window.__clPing = null; return; }}
    el.click();
}}, 5000);
if (!window.__clBye) {{
    // Closing or reloading the tab: the others stop seeing it at once (otherwise it takes the
    // server a few missed pings to notice). A beacon, because a websocket message doesn't get
    // out of a page that is going away; it goes to the backend the way Reflex's own events do.
    window.__clBye = function () {{
        var el = document.getElementById('cl-ping');
        var peer = el && el.dataset.peer;
        if (!peer || !navigator.sendBeacon) return;
        try {{
            var url = new URL('{BYE_PATH}', getBackendURL(env.PING));
            navigator.sendBeacon(url.toString(), JSON.stringify({{ token: getToken(), peer: peer }}));
        }} catch (e) {{}}
    }};
    window.addEventListener('pagehide', window.__clBye);
}}
"""


def page_hooks() -> rx.Component:
    """Still here: a ping every 5 s keeps this tab among the people on the notebook."""
    return rx.el.button(
        id="cl-ping",
        on_click=NotebookState.ping,
        on_mount=rx.call_script(_HOOKS_JS),
        custom_attrs={"data-peer": ME},
        tab_index=-1,
        aria_hidden="true",
        class_name="sr-only",
    )
