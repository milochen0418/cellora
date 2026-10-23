"""One tab's side of a notebook.

Everyone on /nb/<id> works in the same notebook: its cells are the shared document
(:mod:`cellora.docs`), the rest is the notebook's room (:mod:`.room`). ``NotebookState`` is a tab's
own part: which notebook it is on and who it is there, which side panel it shows, the cell it is
on (for the AI), and the commands the page sends to the notebook component. Its events run cells
in the shared kernel, saying who ran them.
"""

from __future__ import annotations

import hmac
import os
import random
import secrets

import reflex as rx

from cellora import rooms, viewers
from cellora.docs import DOCS
from cellora.kernels import KERNELS
from cellora.states.lang import text
from cellora.states.room import NotebookRoom, room_token
from cellora.store import valid_id

try:
    from reflex_ddns_auth import AuthState
except ImportError:  # runs without sign-in: everyone is a guest
    AuthState = None

# With a passcode set, running code needs it once per tab (reading and editing don't).
RUN_PASSCODE = os.environ.get("CELLORA_RUN_PASSCODE", "")


class NotebookState(rx.State):
    """A tab on Cellora: its notebook and who it is there, and what is theirs alone."""

    # The notebook on screen ("" on the list page), and whether the address names none.
    nb_id: str = ""
    gone: bool = False
    # Who this tab is: {"peer", "user", "name", "email", "avatar", "color"}.
    me: dict[str, str] = {}
    _peer: str = ""
    _guest: str = ""
    # The left panel: "outline", "variables", "files" or "" (closed); the AI panel on the right.
    panel: str = "outline"
    ai_open: bool = False
    # The cell this tab is on (the notebook component says), for the AI and "run above/below".
    focus_cell: str = ""
    # Commands to the notebook component: {"seq", "type", ...}; a new seq makes it act.
    command: dict[str, str] = {}
    _seq: int = 0
    # Running code needs CELLORA_RUN_PASSCODE when it is set.
    unlocked: bool = False
    passcode_open: bool = False
    passcode_input: str = ""
    passcode_error: str = ""
    _waiting: list[str] = []
    # Renaming the notebook.
    title_draft: str = ""
    editing_title: bool = False
    # Keyboard shortcuts dialog.
    shortcuts_open: bool = False

    @rx.var
    def run_locked(self) -> bool:
        return bool(RUN_PASSCODE) and not self.unlocked

    # ------------------------------------------------------------ who and where

    async def _identify(self) -> None:
        """Who this tab is: the signed-in account (reflex_ddns_auth), else a guest."""
        email = name = avatar = ""
        if AuthState is not None:
            try:
                auth = await self.get_state(AuthState)
                if not auth._auth_loaded:
                    await auth.load_auth()
                if auth.is_logged_in:
                    email, name, avatar = auth.user_email, auth.user_name or auth.user_email, auth.user_avatar
            except Exception:
                pass
        if not self._peer:
            self._peer = secrets.token_hex(6)
        if email:
            user = email
        else:
            if not self._guest:
                self._guest = str(random.randint(1000, 9999))
            # A Relack guest is signed in with a nickname but no email: keep the nickname.
            user, name = f"guest-{self._guest}", name or f"Guest {self._guest}"
        me = {"peer": self._peer, "user": user, "name": name, "email": email, "avatar": avatar, "color": (self.me or {}).get("color", "")}
        if me != self.me:
            self.me = me

    def _join(self) -> None:
        """Puts this tab among the people on its notebook."""
        me = self.me
        v = viewers.join(self.nb_id, self._peer, self.router.session.client_token, me["user"], me["name"], me.get("avatar", ""))
        if me.get("color") != v.color:
            self.me = {**me, "color": v.color}

    def _by(self) -> dict:
        me = self.me or {}
        return {"name": me.get("name", ""), "color": me.get("color", ""), "peer": me.get("peer", ""), "user": me.get("user", "")}

    async def _room(self) -> NotebookRoom | None:
        """The room of the notebook on screen; None without one (or before it is linked)."""
        if not self.nb_id:
            return None
        room = await self.get_state(NotebookRoom)
        return room if room._linked_to == room_token(self.nb_id) else None

    @rx.event
    async def on_load(self):
        """Opens the notebook in the URL and links this tab to its room."""
        path = str(self.router.url.path or "")
        nb_id = path.rstrip("/").rsplit("/", 1)[-1] if path else ""
        await self._identify()
        was_on = viewers.on(self._peer)
        live = await DOCS.open(nb_id) if valid_id(nb_id) else None
        room = await self.get_state(NotebookRoom)
        if live is None or live.deleted:
            if was_on:
                viewers.leave(self._peer)
                rooms.push_later(was_on)
            self.nb_id, self.gone = "", True
            try:
                return await room._unlink()
            except Exception:  # not linked
                return None
        self.nb_id, self.gone = nb_id, False
        self.focus_cell = ""
        self.editing_title = False
        # A tab back on its notebook (e.g. reconnecting after a restart) is found there again.
        self._join()
        if was_on and was_on != nb_id:
            rooms.push_later(was_on)
        room = await room._link_to(room_token(nb_id))
        rooms.publish(room, nb_id)
        return rx.call_script(f"document.title = {_js_str(live.meta.title + ' · Cellora')}")

    @rx.event
    async def leave_notebook(self):
        """Back to the notebook list: the others stop seeing this tab, and it stops hearing
        about the notebook."""
        left = viewers.leave(self._peer) if self._peer else ""
        self.nb_id = ""
        room = await self.get_state(NotebookRoom)
        if left:
            if room._linked_to == room_token(left):
                rooms.publish(room, left)
            else:
                rooms.push_later(left)
        try:
            return await room._unlink()
        except Exception:  # not linked (e.g. after a server restart)
            return None

    @rx.event
    async def ping(self):
        """Still here: keeps this tab among the people on the notebook, and drops those gone."""
        room = await self._room()
        if room is None:
            return
        if not viewers.touch(self.nb_id, self._peer):
            # Back after a restart, or after being dropped as idle.
            await self._identify()
            self._join()
        viewers.prune(self.nb_id)
        rooms.publish(room, self.nb_id)

    # ------------------------------------------------------------ the notebook component

    def _command(self, kind: str, **fields: str) -> None:
        self._seq += 1
        self.command = {"seq": str(self._seq), "type": kind, **{k: str(v) for k, v in fields.items()}}

    @rx.event
    async def handle_js(self, e: dict):
        """What the notebook component asks for: {"type": ..., ...}."""
        kind = str(e.get("type") or "")
        if kind == "run":
            return await self._run([str(c) for c in e.get("cells") or []])
        if kind == "focus":
            self.focus_cell = str(e.get("cell") or "")
            return None
        if kind == "interrupt":
            return await self.interrupt()
        if kind in ("ai_edit", "ai_fix", "ai_explain", "ai_accept", "ai_reject"):
            from cellora.states.ai import AIState

            cell = str(e.get("cell") or "")
            if kind == "ai_edit":
                return AIState.start_edit(cell, str(e.get("prompt") or ""), "edit")
            if kind == "ai_fix":
                return AIState.start_edit(cell, "", "fix")
            if kind == "ai_explain":
                self.ai_open = True
                return AIState.explain(cell)
            return AIState.clear_draft
        if kind == "toast":
            return rx.toast(str(e.get("text") or ""), position="bottom-center")
        return None

    async def _run(self, cell_ids: list[str]):
        if not self.nb_id or not cell_ids:
            return None
        if RUN_PASSCODE and not self.unlocked:
            self._waiting = cell_ids
            self.passcode_open, self.passcode_error, self.passcode_input = True, "", ""
            return None
        await KERNELS.run(self.nb_id, cell_ids, self._by())
        return None

    async def _cells(self) -> list[str]:
        live = DOCS.get(self.nb_id)
        return live.cell_ids() if live is not None else []

    @rx.event
    async def run_all(self):
        return await self._run(await self._cells())

    @rx.event
    async def run_above(self):
        ids = await self._cells()
        if self.focus_cell in ids:
            return await self._run(ids[: ids.index(self.focus_cell)])

    @rx.event
    async def run_from_here(self):
        ids = await self._cells()
        if self.focus_cell in ids:
            return await self._run(ids[ids.index(self.focus_cell):])

    @rx.event
    async def interrupt(self):
        session = KERNELS.get(self.nb_id)
        if session is not None:
            await session.interrupt()

    @rx.event
    async def restart(self):
        if RUN_PASSCODE and not self.unlocked:
            self.passcode_open = True
            return None
        session = KERNELS.session(self.nb_id)
        await session.restart()
        return rx.toast(await text(self, "kernel_restarted"), position="bottom-center")

    @rx.event
    async def shutdown_kernel(self):
        session = KERNELS.get(self.nb_id)
        if session is not None:
            await session.shutdown()

    @rx.event
    async def clear_outputs(self):
        live = DOCS.get(self.nb_id)
        if live is None:
            return
        with live.doc.transaction():
            for cell_id in live.cell_ids():
                live.clear_outputs(cell_id)
                live.set_execution_count(cell_id, None)

    @rx.event
    async def insert_source(self, source: str, cell_type: str = "code"):
        """A new cell with ``source`` below the cell this tab is on (at the end without one)."""
        live = DOCS.get(self.nb_id)
        if live is None:
            return
        index = live.index_of(self.focus_cell)
        new = live.insert_cell(index + 1 if index >= 0 else len(live.cells), cell_type, source)
        self.focus_cell = new
        self._command("scroll", cell=new)

    @rx.event
    def add_cell(self, cell_type: str):
        self._command("add", cell_type=cell_type)

    @rx.event
    def go_to_cell(self, cell_id: str):
        self._command("scroll", cell=cell_id)

    @rx.event
    def follow(self, peer: str):
        """Jumps to the cell where someone is."""
        self._command("follow", peer=peer)

    # ------------------------------------------------------------ the passcode

    @rx.event
    def set_passcode_input(self, value: str):
        self.passcode_input = value

    @rx.event
    async def unlock(self):
        if RUN_PASSCODE and hmac.compare_digest(self.passcode_input.encode(), RUN_PASSCODE.encode()):
            self.unlocked, self.passcode_open, self.passcode_error, self.passcode_input = True, False, "", ""
            waiting, self._waiting = self._waiting, []
            if waiting:
                return await self._run(waiting)
            return None
        self.passcode_error = await text(self, "passcode_wrong")

    @rx.event
    def close_passcode(self):
        self.passcode_open, self._waiting = False, []

    # ------------------------------------------------------------ the page around it

    @rx.event
    def set_panel(self, name: str):
        self.panel = "" if self.panel == name else name

    @rx.event
    def toggle_ai(self):
        self.ai_open = not self.ai_open

    @rx.event
    def set_shortcuts_open(self, value: bool):
        self.shortcuts_open = value

    @rx.event
    async def start_rename(self):
        room = await self._room()
        self.title_draft = room.title if room is not None else ""
        self.editing_title = True

    @rx.event
    def set_title_draft(self, value: str):
        self.title_draft = value

    @rx.event
    async def rename(self):
        self.editing_title = False
        if not self.nb_id or not self.title_draft.strip():
            return None
        title = DOCS.rename(self.nb_id, self.title_draft)
        room = await self._room()
        if room is not None and title:
            room.title = title
            return rx.call_script(f"document.title = {_js_str(title + ' · Cellora')}")
        return None

    @rx.event
    def cancel_rename(self):
        self.editing_title = False


def _js_str(text: str) -> str:
    import json

    return json.dumps(text)

