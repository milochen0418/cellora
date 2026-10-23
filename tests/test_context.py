"""What the AI is told about a notebook."""

from cellora import nbfile
from cellora.context import describe, earlier_code, variables_comment
from cellora.docs import LiveNotebook
from cellora.store import NotebookMeta


def _live(n: int) -> LiveNotebook:
    cells = []
    for i in range(n):
        cells.append(nbfile.markdown_cell(f"# Part {i}"))
        cells.append(nbfile.code_cell(f"value_{i} = {i}\n" + "x = 1\n" * 200))
    return LiveNotebook(NotebookMeta(id="ctxtest", title="Context"), nbfile.new_notebook("Context", cells))


def test_describe_keeps_the_focus_within_the_budget():
    live = _live(40)
    focus = live.cell_ids()[41]  # code cell of part 20
    text = describe(live, [{"name": "df", "type": "DataFrame", "size": "3 × 2", "columns": ["a: int64", "b: object"]}], focus, "ollama")
    assert len(text) <= 15_000
    assert "the cell the person is on" in text and "value_20 = 20" in text
    assert "left out here" in text
    assert "- df: DataFrame (3 × 2) columns: a: int64, b: object" in text


def test_inline_context():
    live = _live(3)
    last = live.cell_ids()[-1]
    code = earlier_code(live, last)
    assert "value_0 = 0" in code and "value_2" not in code
    assert variables_comment([{"name": "df", "type": "DataFrame", "size": "3 × 2", "columns": ["city: object"]}]) == "# Variables in the kernel:\n# df: DataFrame (3 × 2), columns city: object"
