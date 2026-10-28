"""The shared document of an open notebook: built from an .ipynb, saved back the same."""

import json

from pycrdt import Array, Doc, Text, create_sync_message

from cellora import nbfile
from cellora.docs import LiveNotebook, _apply_terminal, _from_another_life, _replace_text
from cellora.store import NotebookMeta

SAMPLE = {
    "nbformat": 4,
    "nbformat_minor": 5,
    "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"}, "custom": {"x": 1}},
    "cells": [
        {"id": "m1", "cell_type": "markdown", "metadata": {}, "source": ["# Title\n", "Some *text*"]},
        {
            "id": "c1",
            "cell_type": "code",
            "metadata": {"tags": ["a"]},
            "execution_count": 3,
            "source": "print('hi')\n1 + 1",
            "outputs": [
                {"output_type": "stream", "name": "stdout", "text": ["hi\n"]},
                {"output_type": "execute_result", "execution_count": 3, "data": {"text/plain": ["2"]}, "metadata": {}},
            ],
        },
        {"id": "c2", "cell_type": "code", "metadata": {}, "execution_count": None, "source": "1/0", "outputs": [
            {"output_type": "error", "ename": "ZeroDivisionError", "evalue": "division by zero", "traceback": ["\x1b[31mZeroDivisionError\x1b[0m"]},
        ]},
    ],
}


def live_from(sample: dict) -> LiveNotebook:
    nb = nbfile.parse(json.dumps(sample))
    return LiveNotebook(NotebookMeta(id="abc", title="T"), nb)


def test_round_trip_keeps_the_notebook():
    live = live_from(SAMPLE)
    out = live.to_ipynb()
    assert [c["id"] for c in out["cells"]] == ["m1", "c1", "c2"]
    assert out["cells"][0]["source"] == "# Title\nSome *text*"
    c1 = out["cells"][1]
    assert c1["execution_count"] == 3 and isinstance(c1["execution_count"], int)
    assert c1["metadata"] == {"tags": ["a"]}
    assert c1["outputs"][0] == {"output_type": "stream", "name": "stdout", "text": "hi\n"}
    assert c1["outputs"][1]["data"] == {"text/plain": "2"}
    assert out["cells"][2]["outputs"][0]["traceback"] == ["\x1b[31mZeroDivisionError\x1b[0m"]
    assert out["metadata"]["custom"] == {"x": 1}
    assert out["metadata"]["cellora"]["title"] == "T"
    # And it is a valid notebook nbformat writes and reads back.
    again = nbfile.parse(nbfile.dump(out))
    assert again.cells[1].source == "print('hi')\n1 + 1"


def test_cell_edits():
    live = live_from(SAMPLE)
    new = live.insert_cell(1, "code", "x = 1")
    assert live.cell_ids()[1] == new
    assert live.set_source(new, "x = 2  # 😀 changed")
    assert live.source(new) == "x = 2  # 😀 changed"
    live.set_source(new, "y = 2  # 😀 changed again")
    assert live.source(new) == "y = 2  # 😀 changed again"
    live.clear_outputs("c1")
    assert live.to_ipynb()["cells"][2]["outputs"] == []
    live.set_run("c1", state="ok", by="Ann", ended=1.0, duration=0.5)
    saved = live.to_ipynb()["cells"][2]["metadata"]["cellora"]
    assert saved["ran_by"] == "Ann" and saved["duration"] == 0.5
    # Reopened from that file, the run is still told.
    again = LiveNotebook(NotebookMeta(id="abc"), nbfile.parse(nbfile.dump(live.to_ipynb())))
    assert again.run_info("c1")["by"] == "Ann"


def test_streams_join_and_rewrite_their_line():
    live = live_from(SAMPLE)
    live.clear_outputs("c1")
    live.stream("c1", "stdout", "a")
    live.stream("c1", "stdout", "b\n")
    live.stream("c1", "stderr", "warn\n")
    live.stream("c1", "stderr", "more\n")
    outs = live.to_ipynb()["cells"][1]["outputs"]
    assert [o["text"] for o in outs] == ["ab\n", "warn\nmore\n"]
    live.clear_outputs("c1")
    for pct in (10, 50, 100):
        live.stream("c1", "stdout", f"\r{pct}% 😀")
    live.stream("c1", "stdout", "\ndone\n")
    assert live.to_ipynb()["cells"][1]["outputs"][0]["text"] == "100% 😀\ndone\n"


def test_terminal_rules():
    assert _apply_terminal("", "abc\rX") == "Xbc"
    assert _apply_terminal("line1\nabc", "\rxy\n") == "line1\nxyc\n"
    assert _apply_terminal("", "ab\bc") == "ac"
    assert _apply_terminal("a", "\r\nb") == "a\nb"


def test_replace_text_changes_only_the_middle():
    d = Doc()
    d["t"] = t = Text("héllo wörld 😀 end")
    _replace_text(t, str(t), "héllo there wörld 😀 end")
    assert str(t) == "héllo there wörld 😀 end"
    _replace_text(t, str(t), "😀")
    assert str(t) == "😀"
    _replace_text(t, str(t), "")
    assert str(t) == ""


def test_cellora_metadata_survives_a_reload():
    """A cell's title and chart settings (metadata.cellora) are kept through save and load."""
    sample = json.loads(json.dumps(SAMPLE))
    sample["cells"][1]["metadata"] = {"cellora": {"title": "Totals", "views": {"0": {"tab": "chart"}}, "ran_by": "Ann", "ended": 5.0, "duration": 0.1, "state": "ok"}}
    live = live_from(sample)
    assert live.cell("c1")["metadata"]["cellora"] == {"title": "Totals", "views": {"0": {"tab": "chart"}}}
    assert live.run_info("c1")["by"] == "Ann"
    again = LiveNotebook(NotebookMeta(id="abc"), nbfile.parse(nbfile.dump(live.to_ipynb())))
    saved = again.to_ipynb()["cells"][1]["metadata"]["cellora"]
    assert saved["title"] == "Totals" and saved["views"] == {"0": {"tab": "chart"}} and saved["ran_by"] == "Ann"


def _step1(doc: Doc) -> bytes:
    """A browser's first sync message (after its type byte), as y-websocket sends it on connect."""
    return create_sync_message(doc)[1:]


def test_a_copy_of_an_earlier_document_is_told_to_reload():
    server = live_from(SAMPLE)
    # A fresh tab holds nothing yet.
    assert not _from_another_life(_step1(Doc()), server.doc)
    # A tab synced with this document, also after typing on its own while disconnected.
    tab = Doc()
    tab.apply_update(server.doc.get_update())
    assert not _from_another_life(_step1(tab), server.doc)
    tab.get("cells", type=Array)[0]["source"].insert(0, "offline ")
    assert not _from_another_life(_step1(tab), server.doc)
    # The server read the file again (a restart): the same cells, a new document. Merging the
    # tab's copy into it would double every cell, so the tab is told to reload.
    again = live_from(SAMPLE)
    assert _from_another_life(_step1(tab), again.doc)
    # Other sync messages are never refused.
    assert not _from_another_life(b"\x02\x00", again.doc)
    # An empty notebook has nothing to double.
    empty = LiveNotebook(NotebookMeta(id="e"), nbfile.parse(json.dumps({**SAMPLE, "cells": []})))
    assert not _from_another_life(_step1(tab), empty.doc)
