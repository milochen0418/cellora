"""The notebook page (/nb/<id>): top bar, toolbar, the side panels, the notebook, and the AI
panel."""

from __future__ import annotations

import reflex as rx

from cellora.components.ai_panel import ai_panel
from cellora.components.notebook_view import notebook_view
from cellora.components.panels import left_panel
from cellora.components.together import call_button, identity, incoming_call, lang_toggle, page_hooks, presence, share_button
from cellora.states.ai import AIState
from cellora.states.lang import LangState
from cellora.states.notebook import NotebookState
from cellora.states.prefs import PrefsState
from cellora.states.room import NotebookRoom

T = LangState.t


def logo(size: str = "h-7 w-7") -> rx.Component:
    return rx.el.a(
        rx.image(src="/cellora/logo.svg", alt="Cellora", class_name=size),
        href="/",
        title="Cellora",
        class_name="shrink-0",
    )


def title_area() -> rx.Component:
    saved = rx.match(
        NotebookRoom.save_state,
        ("error", rx.el.span(rx.icon("triangle-alert", class_name="h-3.5 w-3.5"), T["not_saved"], title=NotebookRoom.save_error, class_name="flex items-center gap-1 text-xs text-red-600")),
        ("unsaved", rx.el.span(T["saving"], class_name="text-xs text-slate-400")),
        rx.el.span(rx.icon("cloud-check", class_name="h-3.5 w-3.5"), T["saved"], id="save-state", class_name="hidden sm:flex items-center gap-1 text-xs text-slate-400"),
    )
    return rx.el.div(
        rx.el.a(T["notebooks"], href="/", class_name="hidden md:inline text-sm text-slate-400 hover:text-slate-600"),
        rx.el.span("/", class_name="hidden md:inline text-slate-300"),
        rx.cond(
            NotebookState.editing_title,
            rx.el.input(
                value=NotebookState.title_draft,
                on_change=NotebookState.set_title_draft,
                on_blur=NotebookState.rename,
                on_key_down=lambda key: rx.cond(key == "Enter", NotebookState.rename, rx.cond(key == "Escape", NotebookState.cancel_rename, rx.noop())),
                auto_focus=True,
                id="title-input",
                class_name="min-w-0 w-[min(26rem,40vw)] px-2 py-1 text-[15px] font-semibold text-slate-900 border border-indigo-300 rounded-md outline-none",
            ),
            rx.el.button(
                NotebookRoom.title,
                on_click=NotebookState.start_rename,
                id="nb-title",
                title=T["rename"],
                class_name="min-w-0 max-w-[40vw] truncate px-1.5 py-1 rounded-md text-[15px] font-semibold text-slate-900 hover:bg-slate-100",
            ),
        ),
        saved,
        class_name="flex items-center gap-1.5 min-w-0",
    )


def top_bar() -> rx.Component:
    return rx.el.header(
        rx.el.div(logo(), title_area(), class_name="flex items-center gap-3 min-w-0"),
        rx.el.div(
            presence(),
            share_button(),
            call_button(),
            rx.el.div(class_name="cl-sep hidden sm:block"),
            lang_toggle(),
            identity(),
            class_name="flex items-center gap-2",
        ),
        class_name="flex items-center justify-between gap-3 px-4 h-14 bg-white border-b border-slate-200 shrink-0",
    )


def kernel_chip() -> rx.Component:
    base = "h-2 w-2 rounded-full "
    dot = rx.match(
        NotebookRoom.kernel_state,
        ("idle", base + "bg-emerald-500"),
        ("busy", base + "bg-indigo-500 animate-pulse"),
        ("starting", base + "bg-amber-400 animate-pulse"),
        ("restarting", base + "bg-amber-400 animate-pulse"),
        ("dead", base + "bg-red-500"),
        base + "bg-slate-300",
    )
    label = rx.match(
        NotebookRoom.kernel_state,
        ("idle", T["kernel_idle"]),
        ("busy", T["kernel_busy"]),
        ("starting", T["kernel_starting"]),
        ("restarting", T["kernel_restarting"]),
        ("dead", T["kernel_dead"]),
        T["kernel_off"],
    )
    return rx.el.div(
        rx.el.span(class_name=dot),
        rx.el.span("Python · ", class_name="hidden lg:inline text-slate-400"),
        rx.el.span(label, id="kernel-state"),
        rx.cond(NotebookRoom.kernel_queued > 0, rx.el.span(" · ", NotebookRoom.kernel_queued, " ", T["waiting"], class_name="text-slate-500")),
        title=rx.cond(NotebookRoom.kernel_error != "", NotebookRoom.kernel_error, T["kernel_title"]),
        class_name="flex items-center gap-1.5 px-2.5 h-7 rounded-full bg-slate-100 text-xs font-medium text-slate-700 whitespace-nowrap",
    )


def tool(icon: str, label, on_click, *, id: str, title=None, disabled=None, cls: str = "") -> rx.Component:
    return rx.el.button(
        rx.icon(icon, class_name="h-4 w-4"),
        rx.el.span(label, class_name="hidden xl:inline"),
        on_click=on_click,
        id=id,
        title=title if title is not None else label,
        disabled=disabled if disabled is not None else False,
        type="button",
        class_name="cl-tool " + cls,
    )


def open_backend(path) -> rx.event.EventSpec:
    """Opens a path of the backend (downloads): the same host behind re-ddns, another port in a
    local run."""
    return rx.call_script("window.open(new URL(" + rx.Var.create(path).to_string() + ", getBackendURL(env.PING)).toString(), '_blank')")


def download_menu() -> rx.Component:
    return rx.menu.root(
        rx.menu.trigger(
            rx.el.button(rx.icon("download", class_name="h-4 w-4"), rx.el.span(T["download"], class_name="hidden xl:inline"), type="button", id="download-menu", title=T["download"], class_name="cl-tool"),
        ),
        rx.menu.content(
            rx.menu.item(T["download_ipynb"], on_select=open_backend("/_cellora/download/" + NotebookState.nb_id + ".ipynb"), id="download-ipynb"),
            rx.menu.item(T["download_py"], on_select=open_backend("/_cellora/download/" + NotebookState.nb_id + ".py"), id="download-py"),
        ),
    )


def settings_menu() -> rx.Component:
    return rx.menu.root(
        rx.menu.trigger(
            rx.el.button(rx.icon("settings-2", class_name="h-4 w-4"), type="button", id="settings-menu", title=T["settings"], class_name="cl-tool"),
        ),
        rx.menu.content(
            rx.menu.item(
                rx.el.span(rx.cond(PrefsState.ai_inline, "✓ ", "   "), T["pref_ai_inline"], class_name="whitespace-pre"),
                on_select=PrefsState.toggle_ai_inline,
            ),
            rx.menu.item(
                rx.el.span(rx.cond(PrefsState.line_numbers, "✓ ", "   "), T["pref_line_numbers"], class_name="whitespace-pre"),
                on_select=PrefsState.toggle_line_numbers,
            ),
            rx.menu.separator(),
            rx.menu.item(T["shortcuts"], on_select=NotebookState.set_shortcuts_open(True)),
            rx.menu.item(T["shutdown_kernel"], on_select=NotebookState.shutdown_kernel),
        ),
    )


def restart_button() -> rx.Component:
    return rx.alert_dialog.root(
        rx.alert_dialog.trigger(
            rx.el.button(rx.icon("rotate-ccw", class_name="h-4 w-4"), rx.el.span(T["restart"], class_name="hidden xl:inline"), type="button", id="restart-kernel", title=T["restart_title"], class_name="cl-tool"),
        ),
        rx.alert_dialog.content(
            rx.alert_dialog.title(T["restart_q"]),
            rx.alert_dialog.description(T["restart_body"]),
            rx.el.div(
                rx.alert_dialog.cancel(rx.button(T["cancel"], variant="soft", color_scheme="gray")),
                rx.alert_dialog.action(rx.button(T["restart"], on_click=NotebookState.restart, id="restart-confirm")),
                class_name="flex justify-end gap-2 mt-4",
            ),
            max_width="420px",
        ),
    )


def tool_bar() -> rx.Component:
    return rx.el.div(
        rx.el.div(
            tool("chevrons-right", T["run_all"], NotebookState.run_all, id="run-all", cls="!text-indigo-700"),
            tool("arrow-up-to-line", T["run_above"], NotebookState.run_above, id="run-above", disabled=NotebookState.focus_cell == ""),
            tool("arrow-down-to-line", T["run_from_here"], NotebookState.run_from_here, id="run-from-here", disabled=NotebookState.focus_cell == ""),
            tool("square", T["interrupt"], NotebookState.interrupt, id="interrupt", disabled=~NotebookRoom.kernel_busy),
            restart_button(),
            tool("eraser", T["clear_outputs"], NotebookState.clear_outputs, id="clear-outputs"),
            rx.el.div(class_name="cl-sep"),
            tool("plus", T["add_code"], NotebookState.add_cell("code"), id="add-code", title=T["add_code_title"]),
            tool("type", T["add_text"], NotebookState.add_cell("markdown"), id="add-text", title=T["add_text_title"]),
            rx.el.div(class_name="cl-sep"),
            kernel_chip(),
            class_name="flex items-center gap-0.5 min-w-0 overflow-x-auto",
        ),
        rx.el.div(
            download_menu(),
            settings_menu(),
            rx.el.button(
                rx.icon("sparkles", class_name="h-4 w-4"),
                rx.el.span(T["ai_assistant"], class_name="hidden md:inline"),
                on_click=NotebookState.toggle_ai,
                type="button",
                id="ai-toggle",
                class_name=rx.cond(NotebookState.ai_open, "cl-tool is-ai is-on", "cl-tool is-ai"),
            ),
            class_name="flex items-center gap-0.5 shrink-0",
        ),
        class_name="flex items-center justify-between gap-2 px-3 h-11 bg-white/90 backdrop-blur border-b border-slate-200 shrink-0",
    )


def passcode_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title(T["passcode_title"]),
            rx.dialog.description(T["passcode_body"], class_name="text-sm text-slate-600"),
            rx.el.input(
                type="password",
                value=NotebookState.passcode_input,
                on_change=NotebookState.set_passcode_input,
                on_key_down=lambda key: rx.cond(key == "Enter", NotebookState.unlock, rx.noop()),
                auto_focus=True,
                id="passcode-input",
                class_name="mt-3 w-full px-3 py-2 border border-slate-300 rounded-lg outline-none focus:border-indigo-400",
            ),
            rx.cond(NotebookState.passcode_error != "", rx.el.p(NotebookState.passcode_error, class_name="mt-2 text-sm text-red-600")),
            rx.el.div(
                rx.button(T["cancel"], variant="soft", color_scheme="gray", on_click=NotebookState.close_passcode),
                rx.button(T["unlock"], on_click=NotebookState.unlock, id="passcode-ok"),
                class_name="flex justify-end gap-2 mt-4",
            ),
            max_width="420px",
        ),
        open=NotebookState.passcode_open,
        on_open_change=lambda _open: NotebookState.close_passcode,
    )


def _key_row(keys: str, what) -> rx.Component:
    return rx.el.tr(
        rx.el.td(rx.el.kbd(keys, class_name="px-1.5 py-0.5 rounded bg-slate-100 border border-slate-200 text-xs font-mono whitespace-nowrap"), class_name="py-1.5 pr-4"),
        rx.el.td(what, class_name="py-1.5 text-sm text-slate-700"),
    )


def shortcuts_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title(T["shortcuts"]),
            rx.el.div(
                rx.el.p(T["keys_edit"], class_name="mt-2 mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400"),
                rx.el.table(
                    _key_row("Shift Enter", T["key_run_next"]),
                    _key_row("⌘/Ctrl Enter", T["key_run_stay"]),
                    _key_row("Alt Enter", T["key_run_insert"]),
                    _key_row("⌘/Ctrl I", T["key_ai"]),
                    _key_row("Tab", T["key_tab"]),
                    _key_row("⌘/Ctrl Z", T["key_undo"]),
                    _key_row("Esc", T["key_esc"]),
                ),
                rx.el.p(T["keys_command"], class_name="mt-4 mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400"),
                rx.el.table(
                    _key_row("Enter", T["key_enter"]),
                    _key_row("↑ ↓ / K J", T["key_move"]),
                    _key_row("A / B", T["key_ab"]),
                    _key_row("M / Y", T["key_my"]),
                    _key_row("D D", T["key_dd"]),
                    _key_row("Z", T["key_z"]),
                ),
            ),
            rx.el.div(rx.dialog.close(rx.button(T["close"], variant="soft")), class_name="flex justify-end mt-4"),
            max_width="460px",
        ),
        open=NotebookState.shortcuts_open,
        on_open_change=NotebookState.set_shortcuts_open,
    )


def gone_page() -> rx.Component:
    return rx.el.div(
        rx.image(src="/cellora/logo.svg", class_name="h-12 w-12 opacity-60"),
        rx.el.h1(T["gone_title"], class_name="text-xl font-semibold text-slate-800"),
        rx.el.p(T["gone_body"], class_name="text-slate-500"),
        rx.el.a(T["all_notebooks"], href="/", class_name="px-4 py-2 rounded-lg bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-500"),
        class_name="h-screen flex flex-col items-center justify-center gap-4 bg-[#f5f6fa]",
    )


def notebook_page() -> rx.Component:
    body = rx.el.div(
        top_bar(),
        tool_bar(),
        rx.el.div(
            left_panel(),
            rx.el.main(
                rx.cond(
                    NotebookState.nb_id != "",
                    notebook_view(
                        notebook_id=NotebookState.nb_id,
                        me=NotebookState.me,
                        labels=T,
                        draft=AIState.draft,
                        command=NotebookState.command,
                        kernel_state=NotebookRoom.kernel_state,
                        ai_inline=PrefsState.ai_inline & AIState.has_ai,
                        line_numbers=PrefsState.line_numbers,
                        on_event=NotebookState.handle_js,
                    ),
                ),
                id="nb-scroll",
                class_name="flex-1 min-w-0 overflow-y-auto",
            ),
            ai_panel(),
            class_name="flex flex-1 min-h-0",
        ),
        incoming_call(),
        page_hooks(),
        passcode_dialog(),
        shortcuts_dialog(),
        class_name="h-screen flex flex-col bg-[#f5f6fa] font-['Inter']",
    )
    return rx.cond(
        NotebookState.gone | NotebookRoom.gone,
        gone_page(),
        body,
    )
