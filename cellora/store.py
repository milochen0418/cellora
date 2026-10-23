"""Where notebooks are kept: .ipynb files, on disk or as items of a shared collection.

Standalone, notebook ``<id>`` is ``<home>/notebooks/<id>.ipynb`` plus ``<id>.json`` (title,
creator, times, number of cells); ``<home>`` is ``CELLORA_HOME`` (see :mod:`cellora.paths`).

Installed from re_ddns's App Store, the app is given the shared collection ``notebooks``
(re_ddns's Owned Collections; the catalog entry declares it and the install sets
``DDNS_COLLECTIONS_ROOT``) and each notebook is an item of it instead:
``<collection>/by-app/<app>/<id>/notebook.ipynb``, with the title and creator in the item's
manifest. Any app given the collection can read the notebooks, and the App Store's storage page
lists them. See :class:`ItemNotebookStore`.

This module only stores and reads; the notebooks people have open live in :mod:`cellora.docs`.
"""

from __future__ import annotations

import errno
import json
import os
import re
import secrets
import sys
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

from cellora import paths
from cellora.nbfile import DEFAULT_TITLE

try:  # on re_ddns: the notebooks as items of a shared collection (ItemNotebookStore)
    from reflex_ddns_auth.collections import Collection, ItemNotFound
except ImportError:
    Collection = ItemNotFound = None

COLLECTION = "notebooks"
KIND = "notebook.jupyter"
ITEM_FILE = "notebook.ipynb"

# Starts with a letter or digit: a notebook's id is also its item's id in a collection, and the
# token of its shared room (which can't hold "_").
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def valid_id(nb_id: str) -> bool:
    return bool(nb_id) and _ID_RE.match(nb_id) is not None


def new_id() -> str:
    return secrets.token_hex(5)


class NotebookGone(Exception):
    """The stored notebook was deleted outside the app (on re_ddns's storage page)."""


@dataclass
class NotebookMeta:
    id: str
    title: str = DEFAULT_TITLE
    created_at: float = 0.0
    updated_at: float = 0.0
    created_by_email: str = ""
    created_by_name: str = ""
    last_editor: str = ""
    cells: int = 0
    size: int = 0

    @staticmethod
    def from_json(d: dict) -> "NotebookMeta":
        known = {k: d[k] for k in NotebookMeta.__dataclass_fields__ if k in d}
        return NotebookMeta(**known)


class NotebookStore:
    """Notebooks as files. How they are stored is up to the methods below;
    :class:`ItemNotebookStore` keeps them in a shared collection instead."""

    def __init__(self, folder: Optional[Path] = None):
        self.dir = Path(folder or paths.notebooks_dir())
        self.lock = threading.RLock()

    def _ipynb(self, nb_id: str) -> Path:
        return self.dir / f"{nb_id}.ipynb"

    def _json(self, nb_id: str) -> Path:
        return self.dir / f"{nb_id}.json"

    def meta(self, nb_id: str) -> Optional[NotebookMeta]:
        if not valid_id(nb_id):
            return None
        try:
            return NotebookMeta.from_json(json.loads(self._json(nb_id).read_text("utf-8")))
        except (OSError, ValueError, TypeError):
            return None

    def exists(self, nb_id: str) -> bool:
        return valid_id(nb_id) and self._json(nb_id).exists() and self._ipynb(nb_id).exists()

    def list(self) -> List[NotebookMeta]:
        """Every stored notebook, the most recently changed first."""
        out = []
        try:
            files = list(self.dir.glob("*.json"))
        except OSError:
            files = []
        for p in files:
            try:
                meta = NotebookMeta.from_json(json.loads(p.read_text("utf-8")))
            except (OSError, ValueError, TypeError):
                continue
            if valid_id(meta.id) and self._ipynb(meta.id).exists():
                out.append(meta)
        return sorted(out, key=lambda m: m.updated_at, reverse=True)

    def read(self, nb_id: str) -> bytes:
        """The stored .ipynb (OSError when there is none)."""
        if not valid_id(nb_id):
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), nb_id)
        return self._ipynb(nb_id).read_bytes()

    def write(self, meta: NotebookMeta, data: bytes, new: bool = False) -> None:
        """Stores a notebook's file and metadata (``new``: a notebook not stored before)."""
        with self.lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            meta.size = len(data)
            _atomic_write(self._ipynb(meta.id), data)
            _atomic_write(self._json(meta.id), json.dumps(asdict(meta), ensure_ascii=False, indent=1).encode("utf-8"))

    def write_meta(self, meta: NotebookMeta) -> None:
        with self.lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            _atomic_write(self._json(meta.id), json.dumps(asdict(meta), ensure_ascii=False, indent=1).encode("utf-8"))

    def remove(self, nb_id: str) -> None:
        with self.lock:
            for p in (self._ipynb(nb_id), self._json(nb_id)):
                try:
                    p.unlink()
                except OSError:
                    pass


class ItemNotebookStore(NotebookStore):
    """Notebooks as items of a shared collection (re_ddns's Owned Collections): notebook ``<id>``
    is the item ``notebooks:<app>/<id>``, a folder holding ``notebook.ipynb``, with the title,
    creator and number of cells in its manifest. A notebook is published once and then revised
    in place each time it is saved, so its item always holds the notebook as it is now."""

    def __init__(self, items: Any):
        super().__init__(Path(items.own_dir))
        self.items = items

    def _item(self, nb_id: str) -> Any:
        if not valid_id(nb_id):
            return None
        item = self.items.get(self.items.ref(nb_id), remote=False)
        return item if item is not None and item.kind == KIND and not item.replica else None

    def meta(self, nb_id: str) -> Optional[NotebookMeta]:
        item = self._item(nb_id)
        return _notebook_meta(item) if item is not None else None

    def exists(self, nb_id: str) -> bool:
        return self._item(nb_id) is not None

    def list(self) -> List[NotebookMeta]:
        # Copies fetched from another host are that host's notebooks, not ours.
        items = [i for i in self.items.items(kind=KIND, remote=False) if i.owner == self.items.app_id and not i.replica]
        return sorted((_notebook_meta(i) for i in items), key=lambda m: m.updated_at, reverse=True)

    def read(self, nb_id: str) -> bytes:
        item = self._item(nb_id)
        if item is None or item.primary is None:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), nb_id)
        return item.primary.read_bytes()

    def write(self, meta: NotebookMeta, data: bytes, new: bool = False) -> None:
        meta.size = len(data)
        fields = dict(title=meta.title, meta=_item_meta(meta))
        with self.lock:
            try:
                if new:
                    with self.items.publish(meta.id, kind=KIND, primary=ITEM_FILE, **fields) as item:
                        with item.open(ITEM_FILE) as fh:
                            fh.write(data)
                else:
                    with self.items.revise(meta.id, **fields) as rev:
                        with rev.open(ITEM_FILE) as fh:
                            fh.write(data)
            except ItemNotFound:
                raise NotebookGone(meta.id)

    def write_meta(self, meta: NotebookMeta) -> None:
        with self.lock:
            try:
                with self.items.revise(meta.id, title=meta.title, meta=_item_meta(meta)):
                    pass
            except ItemNotFound:
                raise NotebookGone(meta.id)

    def remove(self, nb_id: str) -> None:
        with self.lock:
            self.items.remove(nb_id)


def _item_meta(meta: NotebookMeta) -> dict:
    """What an item's manifest keeps of a notebook's metadata in its ``meta`` (the title, the
    times and the size are the manifest's own)."""
    return {k: v for k, v in asdict(meta).items() if k not in ("id", "title", "created_at", "updated_at", "size")}


def _notebook_meta(item: Any) -> NotebookMeta:
    meta = NotebookMeta.from_json({**(item.meta or {}), "id": item.id, "title": item.title or DEFAULT_TITLE})
    meta.created_at = _timestamp(item.created_at)
    meta.updated_at = _timestamp(item.updated_at)
    meta.size = int(getattr(item, "bytes", 0) or 0)
    return meta


def _timestamp(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def make_store() -> NotebookStore:
    """The notebooks as items of the shared collection when re_ddns gave the app one, else as
    files in ``CELLORA_HOME``."""
    if os.environ.get("DDNS_COLLECTIONS_ROOT") and Collection is not None:
        if hasattr(Collection, "revise"):
            return ItemNotebookStore(Collection(COLLECTION))
        print("Cellora: this reflex_ddns_auth can't revise items; notebooks stay in CELLORA_HOME", file=sys.stderr)
    return NotebookStore()


STORE = make_store()
