"""What the AI is told about a notebook: its cells as they are now (the code, what the outputs
said), the cell the person is on, and the kernel's variables, kept within a size budget."""

from __future__ import annotations

from cellora import nbfile
from cellora.docs import LiveNotebook

# Characters of notebook given to the model (the cell you are on and its neighbours are kept
# whole first; far cells get shortened).
BUDGET = {"claude": 60_000, "ollama": 14_000}
CELL_LIMIT = 4_000


def describe(live: LiveNotebook, variables: list[dict], focus: str = "", provider: str = "ollama") -> str:
    budget = BUDGET.get(provider, 14_000)
    nb = live.to_ipynb()
    cells = nb["cells"]
    focus_at = next((i for i, c in enumerate(cells) if c["id"] == focus), -1)
    blocks: list[tuple[int, str]] = []
    for i, cell in enumerate(cells):
        source = cell["source"]
        if len(source) > CELL_LIMIT:
            source = source[:CELL_LIMIT] + "\n… (cell cut)"
        mark = "  ← the cell the person is on" if i == focus_at else ""
        head = f"### Cell {i + 1} ({cell['cell_type']}){mark}"
        body = [head, source if source.strip() else "(empty)"]
        if cell["cell_type"] == "code":
            out = nbfile.outputs_text(cell, limit=1500)
            if out:
                body.append(f"Output:\n{out}")
            run = (cell.get("metadata") or {}).get("cellora") or {}
            if run.get("state") == "error":
                body.append("(the last run failed)")
        # Distance from the focus decides what goes first when cutting.
        distance = abs(i - focus_at) if focus_at >= 0 else len(cells) - i
        blocks.append((distance, "\n".join(body)))
    title = f"Title: {live.meta.title}\n"
    var_text = _variables(variables)
    room = budget - len(title) - len(var_text)
    keep: set[int] = set()
    used = 0
    for distance, idx in sorted((d, i) for i, (d, _) in enumerate(blocks)):
        size = len(blocks[idx][1]) + 2
        if used + size > room:
            continue
        keep.add(idx)
        used += size
    lines = [title]
    skipped = 0
    for i, (_, text) in enumerate(blocks):
        if i in keep:
            if skipped:
                lines.append(f"({skipped} cell(s) left out here)")
                skipped = 0
            lines.append(text)
        else:
            skipped += 1
    if skipped:
        lines.append(f"({skipped} cell(s) left out here)")
    if var_text:
        lines.append(var_text)
    return "\n\n".join(lines)


def _variables(variables: list[dict]) -> str:
    if not variables:
        return ""
    rows = []
    for v in variables[:60]:
        line = f"- {v.get('name')}: {v.get('type')}"
        if v.get("size"):
            line += f" ({v['size']})"
        if v.get("columns"):
            line += " columns: " + ", ".join(v["columns"][:30])
        elif v.get("preview"):
            line += f" = {v['preview'][:80]}"
        rows.append(line)
    return "### Variables in the kernel\n" + "\n".join(rows)


def earlier_code(live: LiveNotebook, cell_id: str, limit: int = 6_000) -> str:
    """The code of the cells before ``cell_id``, nearest last, for inline suggestions."""
    out: list[str] = []
    size = 0
    index = live.index_of(cell_id)
    cells = list(live.cells)[: index if index >= 0 else len(live.cells)]
    for c in reversed(cells):
        if c.get("cell_type") != "code":
            continue
        source = str(c["source"]).strip()
        if not source or source.startswith("%%"):
            continue
        if size + len(source) > limit:
            break
        out.append(source)
        size += len(source) + 2
    return "\n\n".join(reversed(out))


def variables_comment(variables: list[dict], limit: int = 15) -> str:
    """The kernel's variables as Python comments, put before the code the inline suggestions
    continue, so they use the names and columns that exist."""
    lines = []
    for v in variables[:limit]:
        line = f"# {v.get('name')}: {v.get('type')}"
        if v.get("size"):
            line += f" ({v['size']})"
        if v.get("columns"):
            line += ", columns " + ", ".join(v["columns"][:20])
        lines.append(line)
    return "# Variables in the kernel:\n" + "\n".join(lines) if lines else ""
