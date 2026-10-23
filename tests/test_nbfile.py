"""The .ipynb format: read leniently, written as Jupyter writes it."""

import json

import pytest

from cellora import nbfile


def test_parse_fixes_ids_and_upgrades():
    v3 = {
        "nbformat": 3, "nbformat_minor": 0, "metadata": {},
        "worksheets": [{"cells": [
            {"cell_type": "code", "input": "print(1)", "outputs": [], "language": "python", "metadata": {}},
            {"cell_type": "markdown", "source": "# Hello", "metadata": {}},
        ]}],
    }
    nb = nbfile.parse(json.dumps(v3))
    assert nb.nbformat == 4 and [c.cell_type for c in nb.cells] == ["code", "markdown"]
    assert all(c.id for c in nb.cells)
    dup = {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [
        {"id": "same", "cell_type": "code", "source": "a", "metadata": {}, "outputs": [], "execution_count": None},
        {"id": "same", "cell_type": "code", "source": "b", "metadata": {}, "outputs": [], "execution_count": None},
    ]}
    nb = nbfile.parse(json.dumps(dup))
    assert nb.cells[0].id != nb.cells[1].id


def test_parse_refuses_what_is_not_a_notebook():
    with pytest.raises(ValueError):
        nbfile.parse(b"not json")
    with pytest.raises(ValueError):
        nbfile.parse(b'{"hello": 1}')


def test_title_and_script():
    nb = nbfile.new_notebook("My analysis", [nbfile.markdown_cell("# Heading"), nbfile.code_cell("x = 1")])
    assert nbfile.title_of(nb) == "My analysis"
    nb.metadata["cellora"] = {}
    assert nbfile.title_of(nb) == "Heading"
    script = nbfile.to_python(nb)
    assert "# %% [markdown]\n# # Heading" in script and "# %%\nx = 1" in script


def test_cells_from_markdown():
    text = "# Sales\nSome intro.\n\n```python\nimport pandas as pd\n```\nThen SQL:\n```sql\nSELECT 1\n```\nDone."
    cells = nbfile.cells_from_markdown(text)
    assert [c.cell_type for c in cells] == ["markdown", "code", "markdown", "code", "markdown"]
    assert cells[1].source == "import pandas as pd"
    assert cells[3].source == "%%sql\nSELECT 1"


def test_outputs_text_shows_table_values():
    cell = {"outputs": [{"output_type": "execute_result", "data": {
        "application/vnd.cellora.table+json": {"columns": [{"name": "region"}, {"name": "revenue"}], "rows": [["West", 10]], "total": 1},
        "text/plain": "  region  revenue\n0   West       10",
    }}]}
    text = nbfile.outputs_text(cell)
    assert "columns ['region', 'revenue']" in text and "West" in text
