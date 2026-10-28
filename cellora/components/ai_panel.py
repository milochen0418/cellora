"""The AI assistant panel (right side of a notebook): a conversation about this notebook, with
code you can insert as a new cell or run at once."""

from __future__ import annotations

import reflex as rx

from cellora.states.ai import AIState
from cellora.states.lang import LangState
from cellora.states.notebook import NotebookState

T = LangState.t

# Enter sends, Shift+Enter makes a new line; Enter that ends an IME composition (Chinese,
# Japanese input) doesn't send.
# The conversation follows a growing answer while you are at its bottom (not when you scrolled
# up to read).
_FOLLOW_JS = """
(function () {
    var box = document.getElementById('ai-scroll');
    if (!box || box.__clFollow) return;
    box.__clFollow = true;
    var stick = true;
    box.addEventListener('scroll', function () {
        stick = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
    });
    new MutationObserver(function () {
        if (stick) box.scrollTop = box.scrollHeight;
    }).observe(box, { childList: true, subtree: true, characterData: true });
})();
"""

_ENTER_SENDS_JS = """
(function () {
    var el = document.getElementById('ai-input');
    if (!el || el.__clEnter) return;
    el.__clEnter = true;
    el.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
            e.preventDefault();
            var send = document.getElementById('ai-send');
            if (send) send.click();
        }
    });
})();
"""


def provider_chip() -> rx.Component:
    # With the AI Hub: which model answers each of the notebook's AI uses (set on the hub's page).
    hub_tip = (
        T["ai_hub_tip"] + "\n"
        + T["ai_use_chat"] + ": " + AIState.info["model"] + "\n"
        + T["ai_use_edit"] + ": " + AIState.info["edit_model"] + "\n"
        + T["ai_use_inline"] + ": " + AIState.info["inline_model"]
    )
    return rx.match(
        AIState.info["provider"],
        ("hub", rx.el.span("AI Hub", " · ", AIState.info["model"], title=hub_tip, class_name="px-2 py-0.5 rounded-full bg-violet-50 text-violet-700 text-[11px] font-medium truncate max-w-[14rem]")),
        ("claude", rx.el.span("Claude", title=AIState.info["detail"], class_name="px-2 py-0.5 rounded-full bg-orange-50 text-orange-700 text-[11px] font-medium")),
        ("ollama", rx.el.span(T["ai_local"], " · ", AIState.info["model"], title=AIState.info["detail"], class_name="px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 text-[11px] font-medium truncate max-w-[12rem]")),
        rx.el.span(T["ai_none"], title=AIState.info["detail"], class_name="px-2 py-0.5 rounded-full bg-slate-100 text-slate-500 text-[11px] font-medium"),
    )


# Each part is its own component (rx.memo): a code block and Markdown use React hooks, and the
# list of parts changes length while an answer streams in.
@rx.memo
def chat_code(text: rx.Var[str], lang: rx.Var[str]) -> rx.Component:
    return rx.el.div(
        rx.el.div(
            rx.el.span(lang, class_name="text-[10.5px] font-mono uppercase tracking-wider text-slate-400"),
            rx.el.div(
                rx.el.button(rx.icon("plus", class_name="h-3.5 w-3.5"), LangState.t["insert_cell"], on_click=AIState.insert_code(text, lang), type="button", title=LangState.t["insert_cell_title"], class_name="flex items-center gap-1 px-2 py-0.5 rounded text-[11.5px] font-medium text-indigo-700 hover:bg-indigo-50"),
                rx.el.button(rx.icon("play", class_name="h-3.5 w-3.5"), LangState.t["run"], on_click=AIState.run_code(text, lang), type="button", title=LangState.t["run_cell_title"], class_name="flex items-center gap-1 px-2 py-0.5 rounded text-[11.5px] font-medium text-emerald-700 hover:bg-emerald-50"),
                rx.el.button(rx.icon("copy", class_name="h-3.5 w-3.5"), on_click=[rx.set_clipboard(text), rx.toast(LangState.t["copied"], position="bottom-center")], type="button", title=LangState.t["copy"], class_name="p-1 rounded text-slate-400 hover:text-slate-700 hover:bg-slate-100"),
                class_name="flex items-center gap-0.5",
            ),
            class_name="flex items-center justify-between px-2.5 py-1 bg-slate-50 border-b border-slate-200",
        ),
        rx.code_block(text, language=rx.cond(lang == "sql", "sql", "python"), show_line_numbers=False, wrap_long_lines=True, custom_style={"margin": "0", "fontSize": "12px", "background": "#fbfbfd"}),
        class_name="cl-chat-code my-2 border border-slate-200 rounded-lg overflow-hidden bg-white",
    )


@rx.memo
def chat_text(text: rx.Var[str]) -> rx.Component:
    return rx.el.div(rx.markdown(text), class_name="cl-chat-md")


def part(p) -> rx.Component:
    return rx.cond(p.kind == "code", chat_code(text=p.text, lang=p.lang), chat_text(text=p.text))


def message(m) -> rx.Component:
    user = rx.el.div(
        rx.el.div(m.text, class_name="px-3 py-2 rounded-2xl rounded-br-md bg-indigo-600 text-white text-[13.5px] leading-relaxed whitespace-pre-wrap"),
        class_name="flex justify-end pl-8",
    )
    assistant = rx.el.div(
        rx.el.div(
            rx.icon("sparkles", class_name="h-3.5 w-3.5 text-violet-600"),
            class_name="h-6 w-6 shrink-0 rounded-full bg-violet-50 flex items-center justify-center mt-0.5",
        ),
        rx.el.div(
            rx.foreach(m.parts, part),
            rx.cond(m.status == "streaming", rx.el.span(class_name="cl-spinner")),
            class_name=rx.cond(m.status == "error", "min-w-0 flex-1 text-red-700", "min-w-0 flex-1"),
        ),
        class_name="flex gap-2 pr-2",
    )
    return rx.cond(m.role == "user", user, assistant)


def suggestions() -> rx.Component:
    def chip(key: str) -> rx.Component:
        return rx.el.button(T[key], on_click=AIState.ask_about(T[key]), type="button", class_name="text-left px-3 py-2 rounded-lg border border-slate-200 bg-white text-[12.5px] text-slate-700 hover:border-violet-300 hover:bg-violet-50/50")

    return rx.el.div(
        rx.el.div(
            rx.icon("sparkles", class_name="h-6 w-6 text-violet-500"),
            rx.el.p(T["ai_hello"], class_name="text-sm font-semibold text-slate-800"),
            rx.el.p(T["ai_hello_body"], class_name="text-[12.5px] leading-relaxed text-slate-500"),
            class_name="flex flex-col items-center text-center gap-1.5 px-4 pt-8 pb-4",
        ),
        rx.el.div(
            chip("ai_s_explain"),
            chip("ai_s_next"),
            chip("ai_s_bugs"),
            chip("ai_s_chart"),
            class_name="flex flex-col gap-2 px-3",
        ),
        rx.cond(
            ~AIState.has_ai,
            rx.el.div(
                rx.el.p(AIState.info["detail"], class_name="text-[12px] leading-relaxed text-amber-800"),
                rx.el.button(T["ai_recheck"], on_click=AIState.recheck, type="button", class_name="mt-2 text-[12px] font-medium text-amber-900 underline"),
                class_name="mx-3 mt-4 p-3 rounded-lg bg-amber-50 border border-amber-200",
            ),
        ),
    )


def ai_panel() -> rx.Component:
    return rx.cond(
        NotebookState.ai_open,
        rx.el.aside(
            rx.el.div(
                rx.el.div(
                    rx.icon("sparkles", class_name="h-4 w-4 text-violet-600"),
                    rx.el.span(T["ai_assistant"], class_name="text-sm font-semibold text-slate-800"),
                    provider_chip(),
                    class_name="flex items-center gap-2 min-w-0",
                ),
                rx.el.div(
                    rx.el.button(rx.icon("eraser", class_name="h-4 w-4"), on_click=AIState.clear_chat, type="button", title=T["ai_clear"], class_name="p-1.5 rounded text-slate-400 hover:bg-slate-100 hover:text-slate-700"),
                    rx.el.button(rx.icon("x", class_name="h-4 w-4"), on_click=NotebookState.toggle_ai, type="button", title=T["close"], class_name="p-1.5 rounded text-slate-400 hover:bg-slate-100 hover:text-slate-700"),
                    class_name="flex items-center",
                ),
                class_name="flex items-center justify-between px-3 h-11 border-b border-slate-200 shrink-0",
            ),
            rx.el.div(
                rx.cond(
                    AIState.messages.length() > 0,
                    rx.el.div(rx.foreach(AIState.messages, message), id="ai-messages", class_name="flex flex-col gap-3 px-3 py-3"),
                    suggestions(),
                ),
                id="ai-scroll",
                on_mount=rx.call_script(_FOLLOW_JS),
                class_name="flex-1 overflow-y-auto",
            ),
            rx.el.div(
                # The box keeps its own text (default_value, not value): the server only listens.
                # A server-controlled value is written back after every keystroke, which cuts off
                # an input method (Chinese, Japanese...) in the middle of composing a word. The
                # text comes back on a remount; AIState.ask empties the box when it sends.
                rx.el.textarea(
                    default_value=AIState.question,
                    on_change=AIState.set_question,
                    on_mount=rx.call_script(_ENTER_SENDS_JS),
                    placeholder=T["ai_ask_placeholder"],
                    id="ai-input",
                    rows=3,
                    class_name="w-full resize-none px-3 py-2 text-[13.5px] leading-relaxed border border-slate-300 rounded-xl outline-none focus:border-violet-400 focus:ring-2 focus:ring-violet-100",
                ),
                rx.el.div(
                    rx.el.span(T["ai_context_note"], class_name="text-[11px] text-slate-400"),
                    rx.cond(
                        AIState.busy,
                        rx.el.button(rx.icon("square", class_name="h-3.5 w-3.5"), T["stop"], on_click=AIState.stop, type="button", class_name="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-slate-100 text-slate-700 text-[12.5px] font-medium hover:bg-slate-200"),
                        rx.el.button(rx.icon("send-horizontal", class_name="h-3.5 w-3.5"), T["send"], on_click=AIState.ask, type="button", id="ai-send", disabled=AIState.question.strip() == "", class_name="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-violet-600 text-white text-[12.5px] font-medium hover:bg-violet-500 disabled:opacity-40"),
                    ),
                    class_name="flex items-center justify-between gap-2 mt-1.5",
                ),
                class_name="p-3 border-t border-slate-200 shrink-0",
            ),
            id="ai-panel",
            class_name="flex flex-col w-[min(400px,100vw)] max-md:fixed max-md:inset-y-0 max-md:right-0 max-md:z-40 max-md:shadow-2xl bg-white border-l border-slate-200 shrink-0",
        ),
    )
