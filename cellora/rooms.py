"""Changing a notebook's room (:class:`NotebookRoom`) from outside the tabs' own events: when the
kernel starts, runs or stops, a notebook was saved, or a tab closed.

A Reflex ``SharedState`` changes for everyone when it is changed through one of the tabs linked
to it (``app.modify_state`` with that tab's token), as ReflexGrid's ``push`` and DICOM Viewer's
``with_room`` do. Pushes for one notebook are gathered: many changes within PUSH_DELAY go in one.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

import reflex as rx

from cellora import call, viewers
from cellora.docs import DOCS
from cellora.kernels import KERNELS
from cellora.states.room import NotebookRoom, Variable, room_token

log = logging.getLogger("cellora.rooms")

NOBODY = object()
PUSH_DELAY = 0.12
_pending: dict[str, asyncio.TimerHandle] = {}
_tasks: set[asyncio.Task] = set()


def publish(room: NotebookRoom, nb_id: str) -> None:
    """Shows who is on the notebook, its kernel, whether it is saved, and its call, to everyone on
    it (only what changed)."""
    peers = viewers.tabs(nb_id)
    if list(room.peers) != peers:
        room.peers = peers
    k = KERNELS.info(nb_id)
    for field, value in (("kernel_state", k["state"]), ("kernel_error", k["error"]), ("kernel_queued", k["queued"]), ("kernel_running", k["running"]), ("kernel_started", k["started_at"])):
        if getattr(room, field) != value:
            setattr(room, field, value)
    session = KERNELS.get(nb_id)
    rows = [Variable(name=v.get("name", ""), type=v.get("type", ""), size=v.get("size", ""), preview=v.get("preview", ""), columns=list(v.get("columns") or [])) for v in (session.variables if session else [])]
    if list(room.variables) != rows:
        room.variables = rows
    if nb_id in DOCS.deleted and not room.gone:
        room.gone = True
    live = DOCS.get(nb_id)
    if live is not None:
        state = "gone" if live.deleted else ("error" if live.save_error else ("unsaved" if live.dirty else "saved"))
        if live.deleted and not room.gone:
            room.gone = True
        if room.save_state != state:
            room.save_state = state
        if room.save_error != live.save_error:
            room.save_error = live.save_error
        if room.saved_at != live.saved_at:
            room.saved_at = live.saved_at
        if room.title != live.meta.title:
            room.title = live.meta.title
    call.sync_room(room, nb_id)


async def with_room(nb_id: str, fn: Callable[[NotebookRoom], Any]) -> Any:
    """``fn(room)`` on the notebook's room, through one of the tabs on it; NOBODY when no tab is
    on it. ``fn`` runs under the room's lock and must not await."""
    from reflex.utils.prerequisites import get_app

    from cellora.states.notebook import NotebookState

    app = get_app().app
    for token in viewers.tokens_on(nb_id):
        try:
            context = app.modify_state(rx.BaseStateToken(ident=token, cls=NotebookState))
            root = await context.__aenter__()
        except Exception as e:  # that tab's state can't be had: try the next one
            log.warning("Cannot reach a tab on notebook %s: %s", nb_id, e)
            continue
        found, result = False, None
        try:
            tab = await root.get_state(NotebookState)
            if tab.nb_id == nb_id:
                room = await root.get_state(NotebookRoom)
                if room._linked_to == room_token(nb_id):
                    found, result = True, fn(room)
        except BaseException as error:
            if not await context.__aexit__(type(error), error, error.__traceback__):
                raise
            continue
        await context.__aexit__(None, None, None)
        if found:
            return result
    return NOBODY


async def push(nb_id: str) -> None:
    """Shows the notebook's room as it is now to everyone on it."""
    _pending.pop(nb_id, None)
    try:
        await with_room(nb_id, lambda room: publish(room, nb_id))
    except Exception:
        log.exception("push to notebook %s failed", nb_id)


def push_later(nb_id: str, delay: float = PUSH_DELAY) -> None:
    """``push``, soon (several calls within ``delay`` make one push)."""
    if nb_id in _pending:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _pending[nb_id] = loop.call_later(delay, lambda: _spawn(push(nb_id)))


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# The kernel and the saving tell the rooms.
KERNELS.on_change = push_later


async def _saved(live) -> None:
    push_later(live.id)


def _dirty(live) -> None:
    DOCS.schedule(live)


DOCS.on_saved = _saved
DOCS.on_dirty = _dirty
