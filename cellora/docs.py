"""The notebooks people have open: one shared document per notebook, held by the server.

Each open notebook is a Yjs document (:mod:`pycrdt`, the CRDT JupyterLab's collaboration uses
too). Every browser on the notebook keeps a copy and talks to the server's over a WebSocket
(``/_cellora/yjs/<id>``, the y-websocket protocol): typing in a cell, adding, moving or deleting
cells reaches everyone at once and merges with what the others type at the same moment. The
server is a peer like the browsers, with two differences: its copy is the one that gets saved
(:meth:`Docs.save`, ``SAVE_DELAY`` seconds after a change), and it writes the kernel's outputs
and each cell's run status into the document (:mod:`cellora.kernels`), so they reach everyone
the same way.

Layout of the document (``assets/cellora/notebook.jsx`` reads and writes the same):

    cells: Array of Map, one per cell:
        id               str, the cell id of the .ipynb (nbformat 4.5)
        cell_type        "code" | "markdown" | "raw"
        source           Text
        outputs          Array of Map (code cells), see below
        execution_count  number | None
        metadata         Map of JSON values: the cell's .ipynb metadata
        run              JSON: {"state": idle|queued|running|ok|error|aborted, "by", "color",
                         "queued_at", "started", "ended", "duration", "count"}

    an output Map:
        output_type      "stream" | "display_data" | "execute_result" | "error"
        name, text       (stream) "stdout" | "stderr", Text (appended to while it runs)
        data, metadata   (display_data, execute_result) JSON strings of the mime bundle
        execution_count  (execute_result)
        display_id       (display_data, when it can be updated later)
        ename, evalue    (error)
        traceback        (error) JSON string of the list of lines

Awareness (who is in which cell, carets and selections) goes through the same socket; the server
keeps the latest state of every browser so a newcomer sees everyone at once, and tells the others
when a browser's socket closes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable, Optional

from pycrdt import Array, Awareness, Decoder, Doc, Map, Text, YMessageType, create_awareness_message, create_sync_message, create_update_message, handle_sync_message, read_message
from starlette.websockets import WebSocket, WebSocketDisconnect

from cellora import nbfile, viewers
from cellora.store import STORE, NotebookGone, NotebookMeta, NotebookStore, new_id, valid_id

log = logging.getLogger("cellora.docs")

# A change is saved this long after it happened (more changes meanwhile go with it).
SAVE_DELAY = 2.0
# A notebook nobody has had open (and whose kernel is idle) for this long is saved and dropped
# from memory; the next person to open it reads it from its file.
IDLE_CLOSE = 10 * 60
# A browser that can't keep up (this many messages waiting for it) is disconnected; it reconnects
# and syncs again.
MAX_PENDING = 2000
# Printed text kept per output, so a loop printing forever can't grow the notebook without end.
MAX_STREAM_BYTES = 1_000_000
CUT_NOTE = "\n… (output cut: too long)\n"

OUTPUT_TYPES = ("stream", "display_data", "execute_result", "error")


class Client:
    """A browser's socket on a notebook, with its own queue of messages to send."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        # The tab (its peer id in cellora.viewers), to tell who changes the notebook.
        self.peer = str(ws.query_params.get("peer") or "")[:40]
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        # The awareness clients this socket announced: Yjs clientID -> latest clock.
        self.aware: dict[int, int] = {}
        self.task: Optional[asyncio.Task] = None

    def send(self, data: bytes) -> None:
        if self.queue.qsize() > MAX_PENDING:
            # Too far behind: closing makes it reconnect and sync from scratch.
            self.queue.put_nowait(None)
            return
        self.queue.put_nowait(data)

    async def run(self) -> None:
        while True:
            data = await self.queue.get()
            if data is None:
                try:
                    await self.ws.close(code=1013)
                except Exception:
                    pass
                return
            try:
                await self.ws.send_bytes(data)
            except Exception:
                return


class LiveNotebook:
    """An open notebook: its shared document, the browsers on it, and its saving."""

    def __init__(self, meta: NotebookMeta, nb: Any):
        self.id = meta.id
        self.meta = meta
        # The notebook's own .ipynb metadata (kernelspec, language_info, ...), kept as it was.
        self.nb_metadata: dict = dict(nb.get("metadata") or {})
        self.doc = Doc()
        self.cells: Array = self.doc.get("cells", type=Array)
        self.clients: dict[WebSocket, Client] = {}
        self.awareness = Awareness(self.doc)
        self.awareness.set_local_state(None)  # the server itself is nobody
        self.rev = 0
        self.saved_rev = 0
        self.save_error = ""
        self.saved_at = 0.0
        self.deleted = False
        self.last_active = time.time()
        self.last_editor = ""
        self._origin: Optional[WebSocket] = None
        self._save_handle: Optional[asyncio.TimerHandle] = None
        self._saving = False
        # Called after every change of the document (the outline, the kernel's context...).
        self.on_change: list[Callable[["LiveNotebook"], None]] = []
        with self.doc.transaction():
            for cell in nb.get("cells", []):
                self.cells.append(_cell_map(cell))
        self.doc.observe(self._on_update)

    # ------------------------------------------------------------ sync

    def _on_update(self, event: Any) -> None:
        self.rev += 1
        self.last_active = time.time()
        if self._origin is not None:
            client = self.clients.get(self._origin)
            v = viewers.viewer(self.id, client.peer) if client is not None and client.peer else None
            if v is not None and v.name:
                self.last_editor = v.name
        message = create_update_message(event.update)
        for ws, client in list(self.clients.items()):
            if ws is not self._origin:
                client.send(message)
        if DOCS.on_dirty is not None:
            DOCS.on_dirty(self)
        for fn in list(self.on_change):
            try:
                fn(self)
            except Exception:  # a listener's bug must not break the sync
                log.exception("on_change listener failed")

    @property
    def dirty(self) -> bool:
        return self.rev != self.saved_rev

    async def serve(self, ws: WebSocket) -> None:
        """One browser's socket, from its first sync until it closes."""
        client = Client(ws)
        self.clients[ws] = client
        client.task = asyncio.create_task(client.run())
        client.send(create_sync_message(self.doc))
        others = [cid for cid in self.awareness.states if cid != self.awareness.client_id]
        if others:
            client.send(create_awareness_message(self.awareness.encode_awareness_update(others)))
        try:
            while True:
                data = await ws.receive_bytes()
                if not data:
                    continue
                kind = data[0]
                if kind == YMessageType.SYNC:
                    self._origin = ws
                    try:
                        reply = handle_sync_message(data[1:], self.doc)
                    except Exception as e:
                        log.warning("notebook %s: bad sync message: %s", self.id, e)
                        reply = None
                    finally:
                        self._origin = None
                    if reply is not None:
                        client.send(reply)
                elif kind == YMessageType.AWARENESS:
                    self._awareness(client, data)
                elif kind == 3:  # y-websocket's "query awareness": send everyone's
                    ids = [cid for cid in self.awareness.states if cid != self.awareness.client_id]
                    if ids:
                        client.send(create_awareness_message(self.awareness.encode_awareness_update(ids)))
        except WebSocketDisconnect:
            pass
        except RuntimeError:  # receive after the socket closed
            pass
        except Exception as e:
            log.warning("notebook %s: socket error: %s", self.id, e)
        finally:
            self.clients.pop(ws, None)
            client.queue.put_nowait(None)
            self._goodbye(client)
            self.last_active = time.time()

    def _awareness(self, client: Client, data: bytes) -> None:
        try:
            update = read_message(data[1:])
            for cid, clock, here in _awareness_entries(update):
                if here:
                    client.aware[cid] = clock
                else:
                    client.aware.pop(cid, None)
            self.awareness.apply_awareness_update(update, client.ws)
        except Exception as e:
            log.warning("notebook %s: bad awareness message: %s", self.id, e)
            return
        for ws, other in list(self.clients.items()):
            if other is not client:
                other.send(data)

    def _goodbye(self, client: Client) -> None:
        """The socket closed: its browser's cursors and places go away for the others."""
        if not client.aware:
            return
        message = create_awareness_message(_awareness_null(client.aware))
        try:
            self.awareness.remove_awareness_states(list(client.aware), "closed")
        except Exception:
            pass
        for other in list(self.clients.values()):
            other.send(message)

    def close_clients(self) -> None:
        for client in list(self.clients.values()):
            client.queue.put_nowait(None)

    # ------------------------------------------------------------ cells

    def cell(self, cell_id: str) -> Optional[Map]:
        for c in self.cells:
            if c.get("id") == cell_id:
                return c
        return None

    def index_of(self, cell_id: str) -> int:
        for i, c in enumerate(self.cells):
            if c.get("id") == cell_id:
                return i
        return -1

    def cell_ids(self) -> list[str]:
        return [str(c.get("id")) for c in self.cells]

    def source(self, cell_id: str) -> Optional[str]:
        c = self.cell(cell_id)
        return str(c["source"]) if c is not None else None

    def cell_type(self, cell_id: str) -> str:
        c = self.cell(cell_id)
        return str(c.get("cell_type") or "") if c is not None else ""

    def run_info(self, cell_id: str) -> dict:
        c = self.cell(cell_id)
        run = c.get("run") if c is not None else None
        return dict(run) if isinstance(run, dict) else {}

    def set_run(self, cell_id: str, **fields: Any) -> None:
        """Changes a cell's run status (what everyone sees on it: queued, running, done...)."""
        c = self.cell(cell_id)
        if c is None:
            return
        run = c.get("run")
        run = dict(run) if isinstance(run, dict) else {}
        run.update({k: v for k, v in fields.items()})
        c["run"] = {k: v for k, v in run.items() if v is not None}

    def set_execution_count(self, cell_id: str, count: Optional[int]) -> None:
        c = self.cell(cell_id)
        if c is not None:
            c["execution_count"] = count

    def outputs(self, cell_id: str) -> Optional[Array]:
        c = self.cell(cell_id)
        if c is None or c.get("cell_type") != "code":
            return None
        outs = c.get("outputs")
        if not isinstance(outs, Array):
            outs = c["outputs"] = Array()
        return outs

    def clear_outputs(self, cell_id: str) -> None:
        outs = self.outputs(cell_id)
        if outs is not None and len(outs):
            outs.clear()

    def add_output(self, cell_id: str, output: dict) -> Optional[int]:
        """Appends a display_data / execute_result / error output; returns its index."""
        outs = self.outputs(cell_id)
        if outs is None:
            return None
        outs.append(_output_map(output))
        return len(outs) - 1

    def replace_output(self, cell_id: str, index: int, output: dict) -> None:
        outs = self.outputs(cell_id)
        if outs is None or not 0 <= index < len(outs):
            return
        with self.doc.transaction():
            del outs[index]
            outs.insert(index, _output_map(output))

    def stream(self, cell_id: str, name: str, text: str) -> None:
        """Adds printed text, to the cell's last output when that is the same stream (as Jupyter
        joins them)."""
        outs = self.outputs(cell_id)
        if outs is None or not text:
            return
        last = outs[len(outs) - 1] if len(outs) else None
        if last is None or last.get("output_type") != "stream" or last.get("name") != name:
            outs.append(Map({"output_type": "stream", "name": name, "text": Text(_cut(_apply_terminal("", text)))}))
            return
        ytext = last["text"]
        size = len(ytext)  # UTF-8 bytes, pycrdt's unit for text positions
        if size >= MAX_STREAM_BYTES:
            return  # cut already
        if "\r" not in text and "\b" not in text:
            # Plain printing: just add it at the end.
            if size + len(text.encode("utf-8")) > MAX_STREAM_BYTES:
                text = _cut(str(ytext) + text)[len(str(ytext)):]
            ytext.insert(size, text)
            return
        old = str(ytext)
        new = _cut(_apply_terminal(old, text))
        # Only the last line can change: everything before it stays.
        _replace_text(ytext, old, new, start=old.rfind("\n") + 1)

    def insert_cell(self, index: int, cell_type: str = "code", source: str = "") -> str:
        """A new cell at ``index`` (the end when out of range); returns its id."""
        cell = nbfile.code_cell(source) if cell_type == "code" else nbfile.markdown_cell(source)
        index = len(self.cells) if index < 0 or index > len(self.cells) else index
        self.cells.insert(index, _cell_map(cell))
        return str(cell["id"])

    def set_source(self, cell_id: str, new: str) -> bool:
        """Replaces a cell's text, changing only the part that differs, so the others' carets
        elsewhere in it stay where they are."""
        c = self.cell(cell_id)
        if c is None:
            return False
        ytext = c["source"]
        _replace_text(ytext, str(ytext), new)
        return True

    # ------------------------------------------------------------ the file

    def to_ipynb(self) -> dict:
        """The notebook as an .ipynb (nbformat 4.5) dict."""
        cells = []
        for c in self.cells:
            cell_type = str(c.get("cell_type") or "code")
            md = c.get("metadata")
            metadata = md.to_py() if isinstance(md, Map) else {}
            run = c.get("run")
            if cell_type == "code" and isinstance(run, dict) and run.get("ended"):
                # Who ran it last, when and for how long (Databricks shows the same).
                metadata["cellora"] = {
                    **(metadata.get("cellora") or {}),
                    "ran_by": run.get("by", ""),
                    "ended": run.get("ended"),
                    "duration": run.get("duration"),
                    "state": run.get("state"),
                }
            cell: dict[str, Any] = {
                "id": str(c.get("id") or nbfile.new_cell_id()),
                "cell_type": cell_type,
                "metadata": metadata,
                "source": str(c["source"]) if c.get("source") is not None else "",
            }
            if cell_type == "code":
                count = c.get("execution_count")
                cell["execution_count"] = int(count) if isinstance(count, (int, float)) else None
                outs = c.get("outputs")
                cell["outputs"] = [_output_dict(o) for o in outs] if isinstance(outs, Array) else []
            cells.append(cell)
        metadata = dict(self.nb_metadata)
        metadata["cellora"] = {**(metadata.get("cellora") or {}), "title": self.meta.title}
        return {"nbformat": 4, "nbformat_minor": 5, "metadata": metadata, "cells": cells}

    def cell_count(self) -> int:
        return len(self.cells)


# ------------------------------------------------------------ the registry


class Docs:
    """The open notebooks of this server process."""

    def __init__(self, store: NotebookStore):
        self.store = store
        self._open: dict[str, LiveNotebook] = {}
        self._loading: dict[str, asyncio.Lock] = {}
        # Notebooks deleted while this process ran (their rooms show that they are gone).
        self.deleted: set[str] = set()
        # Called with a notebook after it changed (to schedule its save), and after it was
        # saved or found deleted (to tell the people on it).
        self.on_dirty: Optional[Callable[[LiveNotebook], None]] = None
        self.on_saved: Optional[Callable[[LiveNotebook], Awaitable[Any]]] = None

    def get(self, nb_id: str) -> Optional[LiveNotebook]:
        return self._open.get(nb_id)

    def open_ids(self) -> list[str]:
        return list(self._open)

    async def open(self, nb_id: str) -> Optional[LiveNotebook]:
        """The open notebook, read from its file the first time; None when there is none."""
        live = self._open.get(nb_id)
        if live is not None:
            return live
        if not valid_id(nb_id):
            return None
        lock = self._loading.setdefault(nb_id, asyncio.Lock())
        async with lock:
            live = self._open.get(nb_id)
            if live is not None:
                return live
            loaded = await asyncio.to_thread(self._read, nb_id)
            if loaded is None:
                return None
            meta, nb = loaded
            live = LiveNotebook(meta, nb)
            self._open[nb_id] = live
            return live

    def _read(self, nb_id: str) -> Optional[tuple[NotebookMeta, Any]]:
        meta = self.store.meta(nb_id)
        if meta is None:
            return None
        try:
            nb = nbfile.parse(self.store.read(nb_id))
        except (OSError, ValueError) as e:
            log.warning("notebook %s can't be read: %s", nb_id, e)
            return None
        return meta, nb

    def create(self, nb: Any, title: str, creator: Optional[dict] = None) -> NotebookMeta:
        """Stores a new notebook (blocking: call from a thread)."""
        creator = creator or {}
        now = time.time()
        meta = NotebookMeta(
            id=new_id(),
            title=nbfile.clean_title(title),
            created_at=now,
            updated_at=now,
            created_by_email=creator.get("email", ""),
            created_by_name=creator.get("name", ""),
            last_editor=creator.get("name", ""),
            cells=len(nb.get("cells", [])),
        )
        while self.store.exists(meta.id):
            meta.id = new_id()
        nb.metadata.setdefault("cellora", {})["title"] = meta.title
        self.store.write(meta, nbfile.dump(nb), new=True)
        return meta

    def list(self) -> list[NotebookMeta]:
        """Every notebook, with the open ones as they are now."""
        return self.with_open(self.store.list())

    def with_open(self, stored: list[NotebookMeta]) -> list[NotebookMeta]:
        """The stored notebooks' metadata, with the open ones as they are now (on the event
        loop: the open notebooks belong to it)."""
        out = {m.id: m for m in stored}
        for nb_id, live in list(self._open.items()):
            if live.deleted:
                out.pop(nb_id, None)
            elif nb_id in out:
                out[nb_id] = live.meta
        return sorted(out.values(), key=lambda m: m.updated_at, reverse=True)

    def meta(self, nb_id: str) -> Optional[NotebookMeta]:
        live = self._open.get(nb_id)
        if live is not None:
            return None if live.deleted else live.meta
        return self.store.meta(nb_id)

    def rename(self, nb_id: str, title: str) -> Optional[str]:
        title = nbfile.clean_title(title)
        live = self._open.get(nb_id)
        meta = live.meta if live is not None else self.store.meta(nb_id)
        if meta is None:
            return None
        meta.title = title
        meta.updated_at = time.time()
        if live is not None:
            # Saved with the notebook (the title is in its metadata too).
            live.rev += 1
            self.schedule(live)
        else:
            self.store.write_meta(meta)
        return title

    def delete(self, nb_id: str) -> None:
        self.deleted.add(nb_id)
        live = self._open.pop(nb_id, None)
        if live is not None:
            live.deleted = True
            self._cancel(live)
            live.close_clients()
        self.store.remove(nb_id)

    def file_bytes(self, nb_id: str) -> Optional[bytes]:
        """The notebook's .ipynb as it is now (the open copy when someone has it open)."""
        live = self._open.get(nb_id)
        if live is not None and not live.deleted:
            return nbfile.dump(live.to_ipynb())
        try:
            return self.store.read(nb_id)
        except OSError:
            return None

    # ------------------------------------------------------------ saving

    def schedule(self, live: LiveNotebook) -> None:
        if live.deleted or live._save_handle is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        live._save_handle = loop.call_later(SAVE_DELAY, lambda: asyncio.ensure_future(self.save(live)))

    def _cancel(self, live: LiveNotebook) -> None:
        if live._save_handle is not None:
            live._save_handle.cancel()
            live._save_handle = None

    async def save(self, live: LiveNotebook) -> bool:
        """Writes the notebook if it changed since it was last written."""
        live._save_handle = None
        if live.deleted or not live.dirty:
            return True
        if live._saving:
            self.schedule(live)
            return True
        rev = live.rev
        data = nbfile.dump(live.to_ipynb())
        live.meta.updated_at = time.time()
        live.meta.cells = live.cell_count()
        if live.last_editor:
            live.meta.last_editor = live.last_editor
        live._saving = True
        try:
            await asyncio.to_thread(self.store.write, live.meta, data, False)
        except NotebookGone:
            live.deleted = True
            self.deleted.add(live.id)
            self._open.pop(live.id, None)
            if self.on_saved is not None:
                await self.on_saved(live)
            live.close_clients()
            return False
        except Exception as e:
            live.save_error = str(e) or type(e).__name__
            log.warning("notebook %s not saved: %s", live.id, live.save_error)
            self.schedule(live)
            return False
        finally:
            live._saving = False
        live.saved_rev = rev
        live.save_error = ""
        live.saved_at = time.time()
        if live.dirty:
            self.schedule(live)
        if self.on_saved is not None:
            await self.on_saved(live)
        return True

    def flush(self) -> None:
        """Writes every changed notebook now (at exit; blocking)."""
        for live in list(self._open.values()):
            if live.deleted or not live.dirty:
                continue
            try:
                live.meta.updated_at = time.time()
                live.meta.cells = live.cell_count()
                self.store.write(live.meta, nbfile.dump(live.to_ipynb()), False)
                live.saved_rev = live.rev
            except Exception as e:
                log.warning("notebook %s not saved at exit: %s", live.id, e)

    async def close_idle(self, busy: Callable[[str], bool], now: Optional[float] = None) -> list[str]:
        """Saves and drops the notebooks nobody has open (and whose kernel isn't running)."""
        now = time.time() if now is None else now
        closed = []
        for nb_id, live in list(self._open.items()):
            if live.clients or busy(nb_id) or now - live.last_active < IDLE_CLOSE:
                continue
            if live.dirty and not await self.save(live):
                continue
            if not live.clients and self._open.get(nb_id) is live:
                self._cancel(live)
                del self._open[nb_id]
                closed.append(nb_id)
        return closed


DOCS = Docs(STORE)


async def yjs_endpoint(ws: WebSocket) -> None:
    """``/_cellora/yjs/<id>``: a browser's copy of the notebook, kept in sync with the server's."""
    nb_id = ws.path_params.get("nb_id", "")
    live = await DOCS.open(nb_id) if valid_id(nb_id) else None
    await ws.accept()
    if live is None or live.deleted:
        # Accepted first: a socket refused before accepting reaches the browser as a plain
        # failure, and it would keep trying; 4404 tells it the notebook is gone.
        await ws.close(code=4404)
        return
    await live.serve(ws)


# ------------------------------------------------------------ helpers


def _cell_map(cell: Any) -> Map:
    cell_type = str(cell.get("cell_type") or "code")
    metadata = dict(cell.get("metadata") or {})
    ours = metadata.pop("cellora", None) or {}
    keep = {k: v for k, v in ours.items() if k not in ("ran_by", "ended", "duration", "state")}
    meta_values = {k: _jsonable(v) for k, v in metadata.items()}
    if keep:
        # Cellora's own settings of the cell (its title, how its outputs are shown).
        meta_values["cellora"] = _jsonable(keep)
    fields: dict[str, Any] = {
        "id": str(cell.get("id") or nbfile.new_cell_id()),
        "cell_type": cell_type,
        "source": Text(_text(cell.get("source"))),
        "metadata": Map(meta_values),
    }
    if cell_type == "code":
        fields["outputs"] = Array([_output_map(o) for o in cell.get("outputs") or [] if o.get("output_type") in OUTPUT_TYPES])
        count = cell.get("execution_count")
        fields["execution_count"] = int(count) if isinstance(count, (int, float)) else None
        if ours.get("ended"):
            fields["run"] = {
                "state": ours.get("state") or "ok",
                "by": ours.get("ran_by") or "",
                "ended": ours.get("ended"),
                "duration": ours.get("duration"),
            }
    return Map(fields)


def _output_map(o: Any) -> Map:
    kind = o.get("output_type")
    if kind == "stream":
        return Map({"output_type": "stream", "name": str(o.get("name") or "stdout"), "text": Text(_cut(_text(o.get("text"))))})
    if kind == "error":
        return Map({
            "output_type": "error",
            "ename": str(o.get("ename") or ""),
            "evalue": str(o.get("evalue") or ""),
            "traceback": json.dumps(list(o.get("traceback") or [])),
        })
    fields: dict[str, Any] = {
        "output_type": kind if kind in ("display_data", "execute_result") else "display_data",
        "data": json.dumps(_bundle(o.get("data") or {})),
        "metadata": json.dumps(o.get("metadata") or {}),
    }
    if kind == "execute_result":
        count = o.get("execution_count")
        fields["execution_count"] = int(count) if isinstance(count, (int, float)) else None
    display_id = ((o.get("transient") or {}).get("display_id")) if isinstance(o.get("transient"), dict) else None
    if display_id:
        fields["display_id"] = str(display_id)
    return Map(fields)


def _output_dict(o: Map) -> dict:
    kind = o.get("output_type")
    if kind == "stream":
        return {"output_type": "stream", "name": o.get("name") or "stdout", "text": str(o.get("text") or "")}
    if kind == "error":
        try:
            tb = json.loads(o.get("traceback") or "[]")
        except ValueError:
            tb = []
        return {"output_type": "error", "ename": o.get("ename") or "", "evalue": o.get("evalue") or "", "traceback": tb}
    try:
        data = json.loads(o.get("data") or "{}")
    except ValueError:
        data = {}
    try:
        metadata = json.loads(o.get("metadata") or "{}")
    except ValueError:
        metadata = {}
    out = {"output_type": kind or "display_data", "data": data, "metadata": metadata}
    if kind == "execute_result":
        count = o.get("execution_count")
        out["execution_count"] = int(count) if isinstance(count, (int, float)) else None
    return out


def _bundle(data: dict) -> dict:
    """A mime bundle as JSON: multi-line strings joined (old notebooks split them in lists)."""
    out = {}
    for mime, value in data.items():
        if isinstance(value, list) and all(isinstance(v, str) for v in value) and not mime.endswith("json"):
            value = "".join(value)
        out[mime] = value
    return out


def _text(value: Any) -> str:
    if isinstance(value, list):
        return "".join(str(v) for v in value)
    return "" if value is None else str(value)


def _jsonable(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError):
        return str(value)


def _cut(text: str) -> str:
    """Printed text kept to MAX_STREAM_BYTES, with a note where it was cut."""
    data = text.encode("utf-8")
    if len(data) <= MAX_STREAM_BYTES:
        return text
    return data[: MAX_STREAM_BYTES - len(CUT_NOTE.encode("utf-8"))].decode("utf-8", "ignore") + CUT_NOTE


def _replace_text(ytext: Text, old: str, new: str, start: int = 0) -> None:
    """Turns ``old`` (the text's content) into ``new`` by replacing only the middle that differs.
    ``start``: where they may begin to differ (the part before is known to be the same)."""
    if old == new:
        return
    start = max(0, min(start, len(old), len(new)))
    limit = min(len(old), len(new))
    while start < limit and old[start] == new[start]:
        start += 1
    end_old, end_new = len(old), len(new)
    while end_old > start and end_new > start and old[end_old - 1] == new[end_new - 1]:
        end_old -= 1
        end_new -= 1
    # pycrdt counts text positions in UTF-8 bytes (an emoji is 4), Python in characters.
    at = len(old[:start].encode("utf-8"))
    gone = len(old[start:end_old].encode("utf-8"))
    with ytext.doc.transaction():
        if gone:
            del ytext[at : at + gone]
        if end_new > start:
            ytext.insert(at, new[start:end_new])


def _apply_terminal(old: str, text: str) -> str:
    """``old`` + ``text`` as a terminal shows it: a carriage return goes back to the start of the
    line (progress bars rewrite their line), a backspace takes back a character."""
    if "\r" not in text and "\b" not in text:
        return old + text
    head, _, line = old.rpartition("\n")
    head = head + "\n" if _ else ""
    out = list(line)
    pos = len(out)
    lines: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\r":
            if i + 1 < len(text) and text[i + 1] == "\n":
                lines.append("".join(out))
                out, pos = [], 0
                i += 2
                continue
            pos = 0
        elif ch == "\n":
            lines.append("".join(out))
            out, pos = [], 0
        elif ch == "\b":
            pos = max(0, pos - 1)
        else:
            if pos < len(out):
                out[pos] = ch
            else:
                out.append(ch)
            pos += 1
        i += 1
    done = "\n".join(lines) + ("\n" if lines else "")
    return head + done + "".join(out)


def _awareness_entries(update: bytes) -> list[tuple[int, int, bool]]:
    """(clientID, clock, still here) for each client of an awareness update."""
    decoder = Decoder(update)
    out = []
    for _ in range(decoder.read_var_uint()):
        cid = decoder.read_var_uint()
        clock = decoder.read_var_uint()
        state = decoder.read_var_string()
        out.append((cid, clock, bool(state) and state != "null"))
    return out


def _awareness_null(clients: dict[int, int]) -> bytes:
    """An awareness update saying these clients left: state null, clock + 1."""
    from pycrdt import Encoder

    encoder = Encoder()
    encoder.write_var_uint(len(clients))
    for cid, clock in clients.items():
        encoder.write_var_uint(cid)
        encoder.write_var_uint(clock + 1)
        encoder.write_var_string("null")
    return encoder.to_bytes()
