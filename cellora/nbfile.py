"""The notebook file: Jupyter's .ipynb (nbformat 4), read leniently and written as Jupyter does.

A Cellora notebook *is* an .ipynb file, so it opens in Jupyter, VS Code, Colab or Databricks as
it is, and theirs open here. What Cellora adds lives under ``metadata.cellora`` (the title) and,
per cell, ``metadata.cellora`` (who ran it, when, for how long; the table or chart an output is
shown as), which other tools ignore.
"""

from __future__ import annotations

import json
import re
import secrets
from typing import Any

import nbformat
from nbformat import v4
from nbformat.notebooknode import NotebookNode

KERNELSPEC = {"name": "python3", "display_name": "Python 3 (ipykernel)", "language": "python"}
LANGUAGE_INFO = {"name": "python", "file_extension": ".py", "mimetype": "text/x-python"}
DEFAULT_TITLE = "Untitled notebook"
MAX_TITLE = 120
# nbformat 4.5 cell ids: 1-64 letters, digits, - and _.
_CELL_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def new_cell_id() -> str:
    return secrets.token_hex(4)


def clean_title(title: str) -> str:
    title = " ".join(str(title or "").split())
    return title[:MAX_TITLE] or DEFAULT_TITLE


def code_cell(source: str = "") -> NotebookNode:
    cell = v4.new_code_cell(source)
    cell.id = new_cell_id()
    return cell


def markdown_cell(source: str = "") -> NotebookNode:
    cell = v4.new_markdown_cell(source)
    cell.id = new_cell_id()
    return cell


def new_notebook(title: str = DEFAULT_TITLE, cells: list | None = None) -> NotebookNode:
    nb = v4.new_notebook()
    nb.metadata["kernelspec"] = dict(KERNELSPEC)
    nb.metadata["language_info"] = dict(LANGUAGE_INFO)
    nb.metadata["cellora"] = {"title": clean_title(title)}
    nb.cells = list(cells) if cells else [code_cell()]
    return nb


def parse(data: bytes | str) -> NotebookNode:
    """An .ipynb read as nbformat 4 (older versions are upgraded); every cell gets a valid,
    unique id. Raises ValueError when it isn't a notebook."""
    text = data.decode("utf-8-sig") if isinstance(data, bytes) else data
    try:
        nb = nbformat.reads(text, as_version=4)
    except Exception as e:  # nbformat raises several kinds (NotJSONError, ValidationError, ...)
        raise ValueError(f"Not a Jupyter notebook: {e}") from e
    if not isinstance(nb.get("cells"), list):
        raise ValueError("Not a Jupyter notebook: it has no cells")
    nb.nbformat, nb.nbformat_minor = 4, max(int(nb.get("nbformat_minor") or 0), 5)
    seen: set[str] = set()
    for cell in nb.cells:
        cid = str(cell.get("id") or "")
        if not _CELL_ID.match(cid) or cid in seen:
            cid = new_cell_id()
            while cid in seen:
                cid = new_cell_id()
            cell["id"] = cid
        seen.add(cid)
        if cell.get("cell_type") not in ("code", "markdown", "raw"):
            cell["cell_type"] = "raw"
        cell.setdefault("metadata", {})
        if cell["cell_type"] == "code":
            cell.setdefault("outputs", [])
            cell.setdefault("execution_count", None)
    nb.metadata.setdefault("kernelspec", dict(KERNELSPEC))
    nb.metadata.setdefault("language_info", dict(LANGUAGE_INFO))
    return nb


def dump(nb: NotebookNode | dict) -> bytes:
    """The notebook as Jupyter writes it (1-space indents, sorted keys, lines split)."""
    node = nb if isinstance(nb, NotebookNode) else nbformat.from_dict(nb)
    return (nbformat.writes(node, version=4) + "\n").encode("utf-8")


def title_of(nb: NotebookNode, fallback: str = DEFAULT_TITLE) -> str:
    """The notebook's title: ours, else its first Markdown heading, else ``fallback``."""
    title = ((nb.get("metadata") or {}).get("cellora") or {}).get("title")
    if title:
        return clean_title(title)
    for cell in nb.get("cells", []):
        if cell.get("cell_type") == "markdown":
            for line in str(cell.get("source") or "").splitlines():
                m = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
                if m:
                    return clean_title(m.group(1))
    return clean_title(fallback)


def to_python(nb: NotebookNode) -> str:
    """The notebook as a percent-format script (``# %%`` cells), as VS Code and Jupytext read it."""
    parts = [f"# {title_of(nb)}"]
    for cell in nb.get("cells", []):
        source = str(cell.get("source") or "")
        if cell.get("cell_type") == "code":
            parts.append("# %%\n" + source)
        elif cell.get("cell_type") == "markdown":
            parts.append("# %% [markdown]\n" + "\n".join(("# " + line).rstrip() for line in source.splitlines()))
    return "\n\n".join(parts) + "\n"


_FENCE = re.compile(r"^```[ \t]*(\w[\w+-]*)?[ \t]*$")


def cells_from_markdown(text: str) -> list[NotebookNode]:
    """Cells from a Markdown answer (AI made notebooks): every fenced Python block is a code cell,
    the text between them Markdown cells."""
    cells: list[NotebookNode] = []
    prose: list[str] = []
    code: list[str] | None = None

    def flush_prose() -> None:
        body = "\n".join(prose).strip()
        if body:
            cells.append(markdown_cell(body))
        prose.clear()

    for line in text.splitlines():
        m = _FENCE.match(line.strip())
        if code is None and m:
            lang = (m.group(1) or "python").lower()
            if lang in ("python", "py", "python3", "ipython", "sql"):
                flush_prose()
                code = ["%%sql"] if lang == "sql" else []
                continue
        if code is not None and line.strip() == "```":
            body = "\n".join(code).strip("\n")
            if body.strip():
                cells.append(code_cell(body))
            code = None
            continue
        (code if code is not None else prose).append(line)
    if code is not None and "\n".join(code).strip():
        cells.append(code_cell("\n".join(code).strip("\n")))
    flush_prose()
    return cells


def outputs_text(cell: Any, limit: int = 600) -> str:
    """What a code cell's outputs say, as plain text (for the AI's view of the notebook)."""
    out: list[str] = []
    for o in cell.get("outputs", []) or []:
        kind = o.get("output_type")
        if kind == "stream":
            out.append(str(o.get("text") or ""))
        elif kind == "error":
            tb = o.get("traceback") or []
            last = _strip_ansi("\n".join(tb[-3:])) if tb else ""
            out.append(f"{o.get('ename')}: {o.get('evalue')}\n{last}")
        elif kind in ("execute_result", "display_data"):
            data = o.get("data") or {}
            if "application/vnd.cellora.table+json" in data:
                table = data["application/vnd.cellora.table+json"]
                if isinstance(table, str):
                    try:
                        table = json.loads(table)
                    except ValueError:
                        table = {}
                table = table or {}
                cols = [c.get("name") for c in table.get("columns", [])]
                head = f"[table: {table.get('total', '?')} rows; columns {cols}]"
                # The values too (pandas' own text of it), so the AI can answer from them.
                text = str(data.get("text/plain") or "")
                if not text:
                    rows = table.get("rows", [])[:15]
                    text = "\n".join(", ".join("" if v is None else str(v) for v in r) for r in rows)
                out.append(f"{head}\n{text}")
            elif "text/plain" in data:
                out.append(str(data["text/plain"]))
            elif any(k.startswith("image/") for k in data):
                out.append("[image]")
    text = "\n".join(s.rstrip() for s in out if s)
    return text if len(text) <= limit else text[: limit - 20] + "\n… (output cut)"


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)
