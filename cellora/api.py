"""The backend's own HTTP routes (next to Reflex's), all under ``/_cellora/`` (backend-paths.txt
sends them to the backend behind re-ddns' nginx).

- ``WS /_cellora/yjs/<id>``: a browser's copy of the notebook, kept in sync (:mod:`cellora.docs`).
- ``POST /_cellora/bye``: a closing tab says goodbye with ``navigator.sendBeacon`` (a websocket
  message wouldn't get out of a page that is going away).
- ``POST /_cellora/complete``, ``/_cellora/inspect``: the kernel's completions and docs for the
  editor; ``POST /_cellora/inline``: the AI's grey suggestion at the cursor.
- ``GET /_cellora/download/<id>.ipynb`` (or ``.py``): the notebook as a file.
- ``GET /_cellora/files/<path>``: a file of the workspace (the Files panel's downloads).

The POSTs name the tab by its Reflex client token and peer id, which only that tab knows: a
request counts only for a tab on that notebook. Their bodies are JSON sent as text/plain, so the
browser needs no CORS preflight when the backend is on another port (local development).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import sys

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route, WebSocketRoute

from cellora import ai, context, nbfile, rooms, viewers
from cellora.docs import DOCS, yjs_endpoint
from cellora.kernels import KERNELS

log = logging.getLogger("cellora.api")

BYE_PATH = "/_cellora/bye"
JANITOR_EVERY = 20.0


async def _body(request: Request, limit: int = 2_000_000) -> dict:
    raw = await request.body()
    if len(raw) > limit:
        return {}
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _caller(data: dict) -> str:
    """The notebook of the tab that sent the request ("" when it isn't on one)."""
    token, peer, nb_id = str(data.get("token") or ""), str(data.get("peer") or ""), str(data.get("nb") or "")
    found = viewers.find(token, peer) if token and peer else ""
    return found if found and found == nb_id else ""


async def bye(request: Request) -> Response:
    data = await _body(request, 4096)
    token, peer = str(data.get("token") or ""), str(data.get("peer") or "")
    nb_id = viewers.find(token, peer) if token and peer else ""
    if nb_id:
        viewers.leave(peer)
        rooms.push_later(nb_id)
    return Response(status_code=204)


async def complete(request: Request) -> Response:
    data = await _body(request)
    nb_id = _caller(data)
    session = KERNELS.get(nb_id) if nb_id else None
    if session is None:
        return JSONResponse({"matches": [], "types": []})
    code = str(data.get("code") or "")
    cursor = max(0, min(int(data.get("cursor") or 0), len(code)))
    return JSONResponse(await session.complete(code, cursor))


async def inspect(request: Request) -> Response:
    data = await _body(request)
    nb_id = _caller(data)
    session = KERNELS.get(nb_id) if nb_id else None
    if session is None:
        return JSONResponse({"text": ""})
    code = str(data.get("code") or "")
    cursor = max(0, min(int(data.get("cursor") or 0), len(code)))
    return JSONResponse({"text": await session.inspect(code, cursor)})


async def inline(request: Request) -> Response:
    data = await _body(request)
    nb_id = _caller(data)
    live = DOCS.get(nb_id) if nb_id else None
    if live is None:
        return JSONResponse({"text": ""})
    prefix = str(data.get("prefix") or "")[-6000:]
    suffix = str(data.get("suffix") or "")[:2000]
    earlier = context.earlier_code(live, str(data.get("cell") or ""))
    session = KERNELS.get(nb_id)
    names = context.variables_comment(session.variables) if session else ""
    head = "\n\n".join(x for x in (names, earlier) if x)
    return JSONResponse({"text": await ai.inline(prefix, suffix, head)})


def _filename(title: str, ext: str) -> str:
    name = re.sub(r"[^\w\- .]+", "", title).strip() or "notebook"
    return f"{name[:80]}.{ext}"


async def download(request: Request) -> Response:
    name = request.path_params["name"]
    nb_id, _, ext = name.partition(".")
    meta = DOCS.meta(nb_id)
    data = DOCS.file_bytes(nb_id) if meta is not None else None
    if data is None or ext not in ("ipynb", "py"):
        return Response("No such notebook", status_code=404)
    if ext == "py":
        data = nbfile.to_python(nbfile.parse(data)).encode("utf-8")
    filename = _filename(meta.title, ext)
    media = "application/x-ipynb+json" if ext == "ipynb" else "text/x-python"
    return Response(data, media_type=media, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{_quote(filename)}"})


def _quote(text: str) -> str:
    from urllib.parse import quote

    return quote(text)


async def workspace_file(request: Request) -> Response:
    from cellora.states.files import resolve

    target = resolve(request.path_params["path"])
    if target is None or not target.is_file():
        return Response("No such file", status_code=404)
    return FileResponse(target, filename=target.name)


api = Starlette(
    routes=[
        WebSocketRoute("/_cellora/yjs/{nb_id}", yjs_endpoint),
        Route(BYE_PATH, bye, methods=["POST"]),
        Route("/_cellora/complete", complete, methods=["POST"]),
        Route("/_cellora/inspect", inspect, methods=["POST"]),
        Route("/_cellora/inline", inline, methods=["POST"]),
        Route("/_cellora/download/{name}", download, methods=["GET"]),
        Route("/_cellora/files/{path:path}", workspace_file, methods=["GET"]),
    ]
)


async def _janitor() -> None:
    """Every JANITOR_EVERY seconds: drops tabs that stopped pinging (and tells the others),
    saves and closes notebooks nobody has open, and stops kernels nobody used for long."""
    while True:
        await asyncio.sleep(JANITOR_EVERY)
        try:
            for nb_id in list(viewers.watching()):
                if viewers.prune(nb_id):
                    rooms.push_later(nb_id)
            await DOCS.close_idle(KERNELS.busy)
            await KERNELS.stop_idle(viewers.count)
        except Exception:
            log.exception("janitor failed")


async def _warm_up() -> None:
    """Builds matplotlib's font cache once, in the background: otherwise the first plot of a
    fresh install waits for it (10 seconds and more)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), 300)
    except Exception:
        pass


@contextlib.asynccontextmanager
async def lifespan():
    """Starts the janitor; at shutdown, writes every changed notebook and stops the kernels."""
    task = asyncio.create_task(_janitor())
    warm = asyncio.create_task(_warm_up())
    try:
        yield
    finally:
        task.cancel()
        warm.cancel()
        DOCS.flush()
        try:
            await asyncio.wait_for(KERNELS.shutdown_all(), 10)
        except Exception:
            pass
