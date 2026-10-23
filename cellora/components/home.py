"""The notebook list (/)."""

from __future__ import annotations

import reflex as rx

from cellora.components.page import logo, open_backend
from cellora.components.together import lang_toggle
from cellora.states.ai import AIState
from cellora.states.home import HomeState
from cellora.states.lang import LangState
from cellora.states.notebook import AuthState

T = LangState.t
IMPORT_ID = "ipynb_upload"


def account() -> rx.Component:
    if AuthState is None:
        return rx.fragment()
    return rx.cond(
        AuthState.is_logged_in,
        rx.el.div(
            rx.cond(AuthState.user_avatar != "", rx.image(src=AuthState.user_avatar, class_name="h-7 w-7 rounded-full")),
            rx.el.span(AuthState.user_name, class_name="hidden sm:inline text-sm text-slate-700"),
            rx.el.a(T["sign_out"], href=AuthState.logout_url, class_name="text-xs text-slate-400 hover:underline"),
            class_name="flex items-center gap-2",
        ),
        rx.el.a(T["sign_in"], href=AuthState.login_url, class_name="text-sm font-medium text-indigo-600 hover:underline"),
    )


def header() -> rx.Component:
    return rx.el.header(
        rx.el.div(
            logo("h-8 w-8"),
            rx.el.div(
                rx.el.span("Cellora", class_name="text-lg font-bold tracking-tight text-slate-900"),
                rx.el.span(T["tagline_short"], class_name="hidden sm:block text-xs text-slate-500"),
                class_name="flex flex-col leading-tight",
            ),
            class_name="flex items-center gap-3",
        ),
        rx.el.div(lang_toggle(then=[HomeState.refresh]), account(), class_name="flex items-center gap-3"),
        class_name="flex items-center justify-between px-6 h-16 bg-white border-b border-slate-200",
    )


def person_dot(p) -> rx.Component:
    return rx.cond(
        p.avatar != "",
        rx.image(src=p.avatar, title=p.name, class_name="h-6 w-6 rounded-full border-2 border-white -ml-1.5 first:ml-0 object-cover"),
        rx.el.span(p.initials, title=p.name, class_name="h-6 w-6 rounded-full border-2 border-white -ml-1.5 first:ml-0 flex items-center justify-center text-[9px] font-bold text-white", style={"backgroundColor": p.color}),
    )


def row(r) -> rx.Component:
    return rx.el.div(
        rx.el.a(
            rx.el.div(
                rx.el.div(
                    rx.icon("notebook-text", class_name="h-5 w-5 text-indigo-500"),
                    class_name="h-10 w-10 rounded-xl bg-indigo-50 flex items-center justify-center shrink-0",
                ),
                rx.el.div(
                    rx.el.span(r.title, class_name="text-[15px] font-semibold text-slate-900 truncate group-hover:text-indigo-700"),
                    rx.el.span(
                        T["edited"], " ", r.ago,
                        rx.cond(r.by != "", rx.el.span(" · ", r.by)),
                        " · ", r.cells_text,
                        class_name="text-xs text-slate-500 truncate",
                    ),
                    class_name="flex flex-col min-w-0",
                ),
                class_name="flex items-center gap-3 min-w-0 flex-1",
            ),
            rx.el.div(
                rx.cond(
                    r.people.length() > 0,
                    rx.el.div(
                        rx.el.div(rx.foreach(r.people, person_dot), class_name="flex items-center pl-1.5"),
                        rx.el.span(r.people.length(), " ", T["viewing"], class_name="text-xs font-medium text-emerald-700"),
                        class_name="flex items-center gap-2 px-2 py-1 rounded-full bg-emerald-50",
                    ),
                ),
                rx.cond(
                    (r.kernel == "idle") | (r.kernel == "busy"),
                    rx.el.span(
                        rx.el.span(class_name=rx.cond(r.kernel == "busy", "h-1.5 w-1.5 rounded-full bg-indigo-500 animate-pulse", "h-1.5 w-1.5 rounded-full bg-emerald-500")),
                        T["kernel_on"],
                        class_name="hidden md:flex items-center gap-1.5 text-[11px] text-slate-500",
                    ),
                ),
                class_name="flex items-center gap-3 shrink-0",
            ),
            href="/nb/" + r.id,
            class_name="group flex items-center justify-between gap-4 flex-1 min-w-0 px-4 py-3",
        ),
        rx.menu.root(
            rx.menu.trigger(rx.el.button(rx.icon("ellipsis", class_name="h-4 w-4"), type="button", class_name="p-2 mr-2 rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-700")),
            rx.menu.content(
                rx.menu.item(T["open"], on_select=rx.redirect("/nb/" + r.id)),
                rx.menu.item(T["duplicate"], on_select=HomeState.duplicate(r.id)),
                rx.menu.item(T["download_ipynb"], on_select=open_backend("/_cellora/download/" + r.id + ".ipynb")),
                rx.menu.separator(),
                rx.menu.item(T["delete"], color="red", on_select=HomeState.ask_delete(r.id, r.title)),
            ),
        ),
        class_name="flex items-center bg-white border border-slate-200 rounded-xl hover:border-indigo-200 hover:shadow-sm transition-all",
        custom_attrs={"data-notebook": r.id},
    )


def new_dialog() -> rx.Component:
    option = "flex items-start gap-3 w-full text-left p-3 rounded-xl border border-slate-200 hover:border-indigo-300 hover:bg-indigo-50/40 transition-colors"
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title(T["new_notebook"]),
            rx.el.input(
                value=HomeState.new_title,
                on_change=HomeState.set_new_title,
                placeholder=T["title_optional"],
                id="new-title",
                class_name="mt-2 w-full px-3 py-2 text-sm border border-slate-300 rounded-lg outline-none focus:border-indigo-400",
            ),
            rx.el.div(
                rx.el.button(
                    rx.icon("file-plus", class_name="h-5 w-5 text-slate-500 mt-0.5"),
                    rx.el.div(rx.el.p(T["blank"], class_name="text-sm font-semibold text-slate-800"), rx.el.p(T["blank_body"], class_name="text-xs text-slate-500")),
                    on_click=HomeState.create_blank,
                    type="button",
                    id="create-blank",
                    class_name=option,
                ),
                rx.el.button(
                    rx.icon("graduation-cap", class_name="h-5 w-5 text-indigo-500 mt-0.5"),
                    rx.el.div(rx.el.p(T["tour"], class_name="text-sm font-semibold text-slate-800"), rx.el.p(T["tour_body"], class_name="text-xs text-slate-500")),
                    on_click=HomeState.create_tour,
                    type="button",
                    id="create-tour",
                    class_name=option,
                ),
                class_name="grid sm:grid-cols-2 gap-2 mt-3",
            ),
            rx.el.div(
                rx.el.div(
                    rx.icon("sparkles", class_name="h-4 w-4 text-violet-600"),
                    rx.el.span(T["with_ai"], class_name="text-sm font-semibold text-slate-800"),
                    class_name="flex items-center gap-2",
                ),
                rx.el.textarea(
                    value=HomeState.ai_request,
                    on_change=HomeState.set_ai_request,
                    placeholder=T["with_ai_placeholder"],
                    rows=3,
                    id="ai-request",
                    disabled=HomeState.ai_busy | ~AIState.has_ai,
                    class_name="mt-2 w-full resize-none px-3 py-2 text-sm border border-slate-300 rounded-lg outline-none focus:border-violet-400",
                ),
                rx.el.div(
                    rx.cond(
                        HomeState.ai_busy,
                        rx.el.span(rx.el.span(class_name="cl-spinner"), HomeState.ai_progress, class_name="flex items-center gap-2 text-xs text-slate-600"),
                        rx.cond(HomeState.ai_error != "", rx.el.span(HomeState.ai_error, class_name="text-xs text-red-600"), rx.cond(~AIState.has_ai, rx.el.span(AIState.info["detail"], class_name="text-xs text-amber-700"), rx.el.span(""))),
                    ),
                    rx.button(
                        rx.icon("sparkles", class_name="h-4 w-4"),
                        T["create_with_ai"],
                        on_click=HomeState.create_with_ai,
                        disabled=HomeState.ai_busy | (HomeState.ai_request.strip() == "") | ~AIState.has_ai,
                        color_scheme="violet",
                        id="create-ai",
                    ),
                    class_name="flex items-center justify-between gap-3 mt-2",
                ),
                class_name="mt-4 p-3 rounded-xl bg-violet-50/50 border border-violet-100",
            ),
            rx.el.div(rx.dialog.close(rx.button(T["cancel"], variant="soft", color_scheme="gray")), class_name="flex justify-end mt-4"),
            max_width="560px",
        ),
        open=HomeState.new_open,
        on_open_change=HomeState.set_new_open,
    )


def delete_dialog() -> rx.Component:
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title(T["delete_q"]),
            rx.alert_dialog.description(rx.el.span(rx.el.b(HomeState.confirm_title), " ", T["delete_body"])),
            rx.el.div(
                rx.alert_dialog.cancel(rx.button(T["cancel"], variant="soft", color_scheme="gray", on_click=HomeState.cancel_delete)),
                rx.alert_dialog.action(rx.button(T["delete"], color_scheme="red", on_click=HomeState.delete, id="confirm-delete")),
                class_name="flex justify-end gap-2 mt-4",
            ),
            max_width="440px",
        ),
        open=HomeState.confirm_id != "",
    )


_REFRESH_JS = """
if (window.__clHome) clearInterval(window.__clHome);
window.__clHome = setInterval(function () {
    var el = document.getElementById('cl-home-refresh');
    if (!el) { clearInterval(window.__clHome); window.__clHome = null; return; }
    if (!document.hidden) el.click();
}, 8000);
"""


def home_page() -> rx.Component:
    return rx.el.div(
        header(),
        rx.el.main(
            rx.el.section(
                rx.el.div(
                    rx.el.h1(T["notebooks"], class_name="text-2xl font-bold tracking-tight text-slate-900"),
                    rx.el.p(T["tagline"], class_name="mt-1 text-sm text-slate-500 max-w-xl"),
                ),
                rx.el.div(
                    rx.upload(
                        rx.el.button(rx.icon("upload", class_name="h-4 w-4"), T["import_ipynb"], type="button", class_name="flex items-center gap-2 h-9 px-3 rounded-lg border border-slate-300 bg-white text-sm font-medium text-slate-700 hover:bg-slate-50"),
                        id=IMPORT_ID,
                        multiple=True,
                        on_drop=HomeState.upload(rx.upload_files(upload_id=IMPORT_ID)),
                        border="0",
                        padding="0",
                    ),
                    rx.el.button(rx.icon("plus", class_name="h-4 w-4"), T["new_notebook"], on_click=HomeState.open_new, type="button", id="new-notebook", class_name="flex items-center gap-2 h-9 px-4 rounded-lg bg-indigo-600 text-white text-sm font-semibold hover:bg-indigo-500 shadow-sm"),
                    class_name="flex items-center gap-2",
                ),
                class_name="flex flex-wrap items-end justify-between gap-4",
            ),
            rx.cond(HomeState.upload_error != "", rx.el.p(HomeState.upload_error, class_name="mt-3 text-sm text-red-600")),
            rx.el.div(
                rx.icon("search", class_name="h-4 w-4 text-slate-400"),
                rx.el.input(value=HomeState.query, on_change=HomeState.set_query, placeholder=T["search"], id="search", class_name="flex-1 bg-transparent outline-none text-sm"),
                class_name="mt-6 flex items-center gap-2 h-10 px-3 rounded-lg border border-slate-200 bg-white focus-within:border-indigo-300",
            ),
            rx.cond(
                HomeState.shown.length() > 0,
                rx.el.div(rx.foreach(HomeState.shown, row), id="notebook-list", class_name="mt-4 flex flex-col gap-2"),
                rx.cond(
                    HomeState.loaded,
                    rx.el.div(
                        rx.image(src="/cellora/logo.svg", class_name="h-14 w-14 opacity-80"),
                        rx.el.p(rx.cond(HomeState.query != "", T["no_match"], T["no_notebooks"]), class_name="text-slate-600 font-medium"),
                        rx.cond(
                            HomeState.query == "",
                            rx.el.div(
                                rx.button(rx.icon("graduation-cap", class_name="h-4 w-4"), T["tour"], on_click=HomeState.create_tour, id="empty-tour"),
                                rx.button(T["blank"], variant="soft", on_click=HomeState.create_blank),
                                class_name="flex gap-2",
                            ),
                        ),
                        class_name="mt-16 flex flex-col items-center gap-4",
                    ),
                ),
            ),
            rx.el.div(
                rx.el.div(rx.icon("users", class_name="h-4 w-4 text-indigo-500"), rx.el.span(T["feature_together"]), class_name="flex items-center gap-2"),
                rx.el.div(rx.icon("sparkles", class_name="h-4 w-4 text-violet-500"), rx.el.span(T["feature_ai"]), class_name="flex items-center gap-2"),
                rx.el.div(rx.icon("table", class_name="h-4 w-4 text-emerald-500"), rx.el.span(T["feature_data"]), class_name="flex items-center gap-2"),
                rx.el.div(rx.icon("file-code-2", class_name="h-4 w-4 text-amber-500"), rx.el.span(T["feature_jupyter"]), class_name="flex items-center gap-2"),
                class_name="mt-12 grid sm:grid-cols-2 gap-3 text-[13px] text-slate-500",
            ),
            class_name="max-w-4xl mx-auto px-6 py-8",
        ),
        new_dialog(),
        delete_dialog(),
        rx.el.button(id="cl-home-refresh", on_click=HomeState.refresh, on_mount=rx.call_script(_REFRESH_JS), tab_index=-1, aria_hidden="true", class_name="sr-only"),
        class_name="min-h-screen bg-[#f5f6fa] font-['Inter']",
    )
