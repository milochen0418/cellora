"""The left side panels of a notebook: its outline, the kernel's variables, and the workspace
files."""

from __future__ import annotations

import reflex as rx

from cellora.components.notebook_view import outline_view
from cellora.states.files import FilesState
from cellora.states.lang import LangState
from cellora.states.notebook import NotebookState
from cellora.states.room import NotebookRoom

T = LangState.t
UPLOAD_ID = "files_upload"


def _tab(name: str, icon: str, label) -> rx.Component:
    return rx.el.button(
        rx.icon(icon, class_name="h-4 w-4"),
        rx.el.span(label, class_name="text-[11px]"),
        on_click=[NotebookState.set_panel(name), *([FilesState.refresh] if name == "files" else [])],
        type="button",
        id=f"panel-{name}",
        title=label,
        class_name=rx.cond(
            NotebookState.panel == name,
            "flex flex-col items-center gap-0.5 w-14 py-2 rounded-lg bg-indigo-50 text-indigo-700",
            "flex flex-col items-center gap-0.5 w-14 py-2 rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-700",
        ),
    )


def variable_row(v) -> rx.Component:
    return rx.el.div(
        rx.el.div(
            rx.el.button(
                v.name,
                on_click=[rx.set_clipboard(v.name), rx.toast(T["copied"], position="bottom-center")],
                title=T["copy_name"],
                type="button",
                class_name="font-mono text-[12.5px] font-semibold text-slate-800 truncate hover:text-indigo-700",
            ),
            rx.el.span(v.type, class_name="shrink-0 px-1.5 py-px rounded bg-slate-100 text-[10.5px] text-slate-600 font-mono"),
            rx.el.span(v.size, class_name="ml-auto shrink-0 text-[11px] text-slate-400 tabular-nums"),
            class_name="flex items-center gap-1.5 min-w-0",
        ),
        rx.cond(v.preview != "", rx.el.div(v.preview, class_name="mt-0.5 font-mono text-[11px] text-slate-500 truncate")),
        title=v.columns.join("\n"),
        class_name="px-2.5 py-1.5 rounded-md hover:bg-slate-50 border-b border-slate-100",
    )


def variables_panel() -> rx.Component:
    return rx.el.div(
        rx.cond(
            NotebookRoom.variables.length() > 0,
            rx.el.div(rx.foreach(NotebookRoom.variables, variable_row), id="variables", class_name="flex flex-col"),
            rx.el.p(
                rx.cond(NotebookRoom.kernel_state == "off", T["vars_no_kernel"], T["vars_empty"]),
                class_name="px-2 py-3 text-[12.5px] leading-relaxed text-slate-400",
            ),
        ),
    )


def file_row(f) -> rx.Component:
    icon = rx.cond(f.is_dir, rx.icon("folder", class_name="h-4 w-4 text-amber-500 shrink-0"), rx.icon("file", class_name="h-4 w-4 text-slate-400 shrink-0"))
    name = rx.cond(
        f.is_dir,
        rx.el.button(f.name, on_click=FilesState.open_folder(f.path), type="button", class_name="truncate text-left text-[12.5px] text-slate-800 hover:text-indigo-700"),
        rx.el.span(f.name, class_name="truncate text-[12.5px] text-slate-800"),
    )
    download = rx.el.button(
        rx.icon("download", class_name="h-3.5 w-3.5"),
        on_click=rx.call_script("window.open(new URL('/_cellora/files/' + " + rx.Var.create(f.path).to_string() + ".split('/').map(encodeURIComponent).join('/'), getBackendURL(env.PING)).toString(), '_blank')"),
        title=T["download"],
        type="button",
        class_name="p-1 rounded text-slate-400 hover:text-slate-700 hover:bg-slate-100",
    )
    return rx.el.div(
        icon,
        rx.el.div(name, rx.el.span(f.size, class_name="text-[10.5px] text-slate-400"), class_name="flex flex-col min-w-0 flex-1"),
        rx.cond(
            f.readable,
            rx.el.button(T["use"], on_click=FilesState.use_in_notebook(f.path), title=T["use_title"], type="button", class_name="px-1.5 py-0.5 rounded text-[11px] font-medium text-indigo-600 hover:bg-indigo-50"),
        ),
        rx.cond(~f.is_dir, download),
        rx.el.button(rx.icon("trash-2", class_name="h-3.5 w-3.5"), on_click=FilesState.ask_delete(f.path), title=T["delete"], type="button", class_name="p-1 rounded text-slate-400 hover:text-red-600 hover:bg-red-50"),
        class_name="group flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-slate-50",
    )


def files_panel() -> rx.Component:
    return rx.el.div(
        rx.el.div(
            rx.el.button(rx.icon("house", class_name="h-3.5 w-3.5"), on_click=FilesState.open_folder(""), type="button", class_name="p-1 rounded hover:bg-slate-100 text-slate-500"),
            rx.foreach(FilesState.crumbs, lambda c: rx.el.span("/ ", c, class_name="text-[12px] text-slate-500 truncate")),
            rx.cond(FilesState.folder != "", rx.el.button(rx.icon("corner-left-up", class_name="h-3.5 w-3.5"), on_click=FilesState.up, type="button", class_name="ml-auto p-1 rounded hover:bg-slate-100 text-slate-500")),
            class_name="flex items-center gap-1 px-1 pb-1 min-w-0",
        ),
        rx.upload(
            rx.el.div(
                rx.icon("upload", class_name="h-4 w-4"),
                rx.el.span(T["upload_files"]),
                class_name="flex items-center justify-center gap-2 py-2.5 text-[12.5px] text-slate-500",
            ),
            id=UPLOAD_ID,
            multiple=True,
            on_drop=FilesState.upload(rx.upload_files(upload_id=UPLOAD_ID)),
            class_name="mx-1 mb-2 border border-dashed border-slate-300 rounded-lg cursor-pointer hover:border-indigo-300 hover:bg-indigo-50/40",
        ),
        rx.cond(FilesState.error != "", rx.el.p(FilesState.error, class_name="px-2 pb-2 text-[12px] text-red-600")),
        rx.cond(
            FilesState.rows.length() > 0,
            rx.el.div(rx.foreach(FilesState.rows, file_row), id="files", class_name="flex flex-col"),
            rx.el.p(T["files_empty"], class_name="px-2 py-3 text-[12.5px] leading-relaxed text-slate-400"),
        ),
        rx.alert_dialog.root(
            rx.alert_dialog.content(
                rx.alert_dialog.title(T["delete_file_q"]),
                rx.alert_dialog.description(FilesState.confirm),
                rx.el.div(
                    rx.alert_dialog.cancel(rx.button(T["cancel"], variant="soft", color_scheme="gray", on_click=FilesState.cancel_delete)),
                    rx.alert_dialog.action(rx.button(T["delete"], color_scheme="red", on_click=FilesState.delete)),
                    class_name="flex justify-end gap-2 mt-4",
                ),
                max_width="400px",
            ),
            open=FilesState.confirm != "",
        ),
    )


def left_panel() -> rx.Component:
    title = rx.match(NotebookState.panel, ("outline", T["outline"]), ("variables", T["variables"]), ("files", T["files"]), "")
    return rx.el.aside(
        rx.el.nav(
            _tab("outline", "list-tree", T["outline"]),
            _tab("variables", "braces", T["variables"]),
            _tab("files", "folder-open", T["files"]),
            class_name="flex flex-col items-center gap-1 py-2 px-1 border-r border-slate-200 bg-white",
        ),
        rx.cond(
            NotebookState.panel != "",
            rx.el.div(
                rx.el.div(
                    rx.el.span(title, class_name="text-xs font-semibold uppercase tracking-wider text-slate-500"),
                    rx.el.button(rx.icon("panel-left-close", class_name="h-4 w-4"), on_click=NotebookState.set_panel(NotebookState.panel), type="button", title=T["close"], class_name="p-1 rounded text-slate-400 hover:bg-slate-100"),
                    class_name="flex items-center justify-between px-3 h-10 shrink-0",
                ),
                rx.el.div(
                    rx.match(
                        NotebookState.panel,
                        ("outline", rx.cond(NotebookState.nb_id != "", outline_view(notebook_id=NotebookState.nb_id, peer=NotebookState.me["peer"], labels=T))),
                        ("variables", variables_panel()),
                        ("files", files_panel()),
                        rx.fragment(),
                    ),
                    class_name="flex-1 overflow-y-auto px-2 pb-4",
                ),
                class_name="hidden md:flex flex-col w-64 bg-white border-r border-slate-200",
            ),
        ),
        class_name="hidden md:flex shrink-0",
    )
