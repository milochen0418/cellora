"""Notebooks kept as files (standalone) and as items of a shared collection (on re_ddns)."""

import pytest

from cellora import nbfile
from cellora.store import ItemNotebookStore, NotebookGone, NotebookMeta, NotebookStore, valid_id


def _nb(title: str) -> bytes:
    return nbfile.dump(nbfile.new_notebook(title, [nbfile.code_cell("x = 1")]))


def test_ids():
    assert valid_id("ab12cd34ef")
    assert not valid_id("")
    assert not valid_id("-ab")
    assert not valid_id("Ab")  # room tokens and item ids: lowercase
    assert not valid_id("a_b")


def test_file_store(tmp_path):
    store = NotebookStore(tmp_path / "notebooks")
    assert store.list() == []
    a = NotebookMeta(id="aaaaaaaaaa", title="First", updated_at=1.0, created_by_name="Ann")
    b = NotebookMeta(id="bbbbbbbbbb", title="Second", updated_at=2.0)
    store.write(a, _nb("First"), new=True)
    store.write(b, _nb("Second"), new=True)
    assert [m.id for m in store.list()] == ["bbbbbbbbbb", "aaaaaaaaaa"]
    assert store.meta("aaaaaaaaaa").created_by_name == "Ann"
    assert store.meta("aaaaaaaaaa").size > 0
    assert nbfile.parse(store.read("aaaaaaaaaa")).cells[0].source == "x = 1"
    a.title = "Renamed"
    store.write_meta(a)
    assert store.meta("aaaaaaaaaa").title == "Renamed"
    store.remove("aaaaaaaaaa")
    assert not store.exists("aaaaaaaaaa")
    assert [m.id for m in store.list()] == ["bbbbbbbbbb"]
    with pytest.raises(OSError):
        store.read("../etc")


def test_item_store(tmp_path, monkeypatch):
    collections = pytest.importorskip("reflex_ddns_auth.collections")
    items = collections.Collection("notebooks", app_id="cellora", root=tmp_path / "collections")
    store = ItemNotebookStore(items)
    meta = NotebookMeta(id="cccccccccc", title="Shared", created_by_name="Ann", created_by_email="ann@example.com", cells=1)
    store.write(meta, _nb("Shared"), new=True)
    item = items.get(items.ref("cccccccccc"), remote=False)
    assert item.kind == "notebook.jupyter" and item.primary.name == "notebook.ipynb"
    assert item.title == "Shared" and item.meta["created_by_name"] == "Ann"
    listed = store.list()
    assert [m.id for m in listed] == ["cccccccccc"] and listed[0].title == "Shared"
    # Saving again revises the same item in place.
    meta.title = "Shared v2"
    store.write(meta, nbfile.dump(nbfile.new_notebook("Shared v2", [nbfile.code_cell("y = 2")])))
    again = items.get(items.ref("cccccccccc"), remote=False)
    assert again.revision == 2 and again.title == "Shared v2"
    assert nbfile.parse(store.read("cccccccccc")).cells[0].source == "y = 2"
    # Deleted on the storage page while open: the next save says so.
    items.remove("cccccccccc")
    with pytest.raises(NotebookGone):
        store.write(meta, _nb("x"))
    with pytest.raises(NotebookGone):
        store.write_meta(meta)
