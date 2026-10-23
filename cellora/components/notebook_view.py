"""The notebook itself: a local React component (``assets/cellora/notebook.jsx``) wrapped for
Reflex, and the outline of the left panel, which shares its connection.

The JSX files are plain assets (Reflex copies ``assets/`` into the frontend), imported by
``importable_path``, as ReflexGrid's grid is (see reflex_grid/components/sheet_view.py for why
not shared assets nor ``rx.asset``'s versioned URL). The other modules (ydoc.js, editor.js,
outputs.jsx, md.js) sit next to it and are imported relatively. The npm packages they import are
installed through ``lib_dependencies``; Monaco itself loads from jsdelivr at run time.
"""

import reflex as rx

_JS = rx.asset("cellora/notebook.jsx")
for _name in ("ydoc.js", "editor.js", "outputs.jsx", "md.js", "cellora.css"):
    rx.asset(f"cellora/{_name}")

LIBS = [
    "yjs@13.6.33",
    "y-websocket@3.1.0",
    "y-protocols@1.0.7",
    "lib0@0.2.119",
    "@monaco-editor/loader@1.7.0",
    "marked@18.1.1",
    "marked-katex-extension@5.1.13",
    "katex@0.19.0",
    "dompurify@3.4.17",
    "chart.js@4.6.1",
]


class CelloraNotebook(rx.Component):
    library = _JS.importable_path
    tag = "CelloraNotebook"
    lib_dependencies: list[str] = LIBS

    notebook_id: rx.Var[str]
    # This tab: {"peer", "name", "color", ...}; its carets and cell markers for the others.
    me: rx.Var[dict]
    labels: rx.Var[dict]
    # The AI's change for a cell, shown there as a diff (see AIState.draft).
    draft: rx.Var[dict]
    # Commands from the page: {"seq", "type": "add" | "scroll" | "follow", ...}.
    command: rx.Var[dict]
    kernel_state: rx.Var[str]
    ai_inline: rx.Var[bool]
    line_numbers: rx.Var[bool]
    on_event: rx.EventHandler[lambda e: [e]]


class CelloraOutline(rx.Component):
    library = _JS.importable_path
    tag = "CelloraOutline"
    lib_dependencies: list[str] = LIBS

    notebook_id: rx.Var[str]
    peer: rx.Var[str]
    labels: rx.Var[dict]


notebook_view = CelloraNotebook.create
outline_view = CelloraOutline.create
