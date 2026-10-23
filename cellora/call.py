"""Calls on a notebook, carried out by a call app through the DDNS Intent ``call.join`` (as in
ReflexGrid, CoDoc in MD and DICOM Viewer).

Cellora keeps who is in each notebook's call and rings the people on the notebook; the call
itself runs in the call app, opened in the intent dialog:

    Intent.start(<call app>, "call.join", private={"room": <call id>}, keep_alive=True,
                 single=True, title=..., user=..., name=...)

- Anyone on a notebook can call everyone on it: the others on it get an incoming call (Accept /
  Decline) for RING_SECONDS.
- While the call is on, everyone on the notebook (also people who open it later) sees "Join
  call" with the number of people in it.
- The call dialog is keep-alive (other dialogs minimize it to the tray) and single (joining
  another call closes it), so the call goes on while you work in the notebook.

Whoever starts a call chooses its app among the installed apps that provide ``call.join`` (the
DDNS Intent registry; ``DDNS_INTENT_PROVIDER_CALL_JOIN`` for local development), through
``Intent.choose_app``, which asks only when there are several. The call keeps that app, so
everyone who answers or joins later opens the same app (the same call) without being asked.
``CELLORA_CALL_APP`` names the app for every call instead. The call id is private: anyone
holding it can join the call, so it stays out of the iframe URL and out of the shared room.

The members and the call id live in this process's memory (``CallStore``); what everyone on the
notebook must see (how many are in the call, who is ringing, who answered) goes to the
notebook's ``NotebookRoom``, so it reaches all of them at once.
"""

from __future__ import annotations

import os
import secrets
import time
from typing import Dict, List, Optional, TypedDict

import reflex as rx

from cellora import viewers
from cellora.states.room import NotebookRoom

try:
    from reflex_ddns_auth.intent import Intent, IntentState

    CALLS_ENABLED = True
except ImportError:  # the app runs without reflex_ddns_auth: no calls
    CALLS_ENABLED = False

CALL_APP = os.environ.get("CELLORA_CALL_APP") or None
CALL_ACTION = "call.join"
# How long a new call rings the people on the notebook.
RING_SECONDS = 45
# A tab in a call refreshes its entry this often...
TOUCH_SECONDS = 15
# ...and is dropped after this long without a heartbeat (closed tab, lost network).
# Generous because background tabs get their timers throttled.
STALE_SECONDS = 150
# The dialog opens one event after join_call; don't mistake that gap for a closed dialog.
OPENING_GRACE_SECONDS = 10


class Member(TypedDict):
    id: str
    name: str


class NotebookCall(TypedDict):
    call_id: str
    # The call app chosen by whoever started the call: everyone joins through it.
    app: str
    started_by_name: str
    started_at: float
    ringing: bool
    # Client token -> who; a person may be in the call from several tabs.
    members: Dict[str, Member]
    last_seen: Dict[str, float]
    # User ids who joined or declined: they are not rung (again).
    responded: List[str]
    # User ids on the notebook when the call started: the people it rings (not those who come later).
    invited: List[str]


class CallStore:
    """The call of each notebook, at most one."""

    calls: Dict[str, NotebookCall] = {}

    @classmethod
    def cleanup(cls, nb_id: str, now: float) -> None:
        """Drop members gone silent, stop ringing after RING_SECONDS, end an empty call."""
        call = cls.calls.get(nb_id)
        if call is None:
            return
        for token, seen in list(call["last_seen"].items()):
            if now - seen > STALE_SECONDS:
                call["members"].pop(token, None)
                call["last_seen"].pop(token, None)
        if call["ringing"] and now - call["started_at"] > RING_SECONDS:
            call["ringing"] = False
        if not call["members"]:
            del cls.calls[nb_id]

    @classmethod
    def join(cls, nb_id: str, token: str, member: Member, now: float, app: str, call_id: str = "", invited: Optional[List[str]] = None) -> NotebookCall:
        """Join the notebook's call, starting it in ``app`` (ringing ``invited``) if there is none."""
        call = cls.calls.get(nb_id)
        if call is None:
            call = NotebookCall(
                call_id=call_id or secrets.token_urlsafe(12),
                app=app,
                started_by_name=member["name"],
                started_at=now,
                # A call put back after being dropped (call_id given) doesn't ring again.
                ringing=not call_id,
                members={},
                last_seen={},
                responded=[],
                invited=list(invited or []),
            )
            cls.calls[nb_id] = call
        call["members"][token] = member
        call["last_seen"][token] = now
        if member["id"] not in call["responded"]:
            call["responded"].append(member["id"])
        return call

    @classmethod
    def leave(cls, nb_id: str, token: str) -> None:
        """Take a tab out of the notebook's call; the call ends with its last member."""
        call = cls.calls.get(nb_id)
        if call is None:
            return
        call["members"].pop(token, None)
        call["last_seen"].pop(token, None)
        if not call["members"]:
            del cls.calls[nb_id]

    @classmethod
    def decline(cls, nb_id: str, user_id: str) -> None:
        call = cls.calls.get(nb_id)
        if call is not None and user_id not in call["responded"]:
            call["responded"].append(user_id)


def sync_room(room: NotebookRoom, nb_id: str) -> None:
    """Shows the notebook's call as it is now to everyone on it (only what changed)."""
    CallStore.cleanup(nb_id, time.time())
    call = CallStore.calls.get(nb_id)
    count = len(call["members"]) if call else 0
    ringing_from = call["started_by_name"] if call and call["ringing"] else ""
    responded = list(call["responded"]) if call else []
    invited = list(call["invited"]) if call and call["ringing"] else []
    if room.call_count != count:
        room.call_count = count
    if room.call_ringing_from != ringing_from:
        room.call_ringing_from = ringing_from
    if list(room.call_responded) != responded:
        room.call_responded = responded
    if list(room.call_invited) != invited:
        room.call_invited = invited


class CallState(rx.State):
    """This tab's side of notebook calls."""

    # Notebook whose call this tab is in ("" when in no call).
    current_notebook: str = ""
    _call_id: str = ""
    # The call's app, to put the call back as it was.
    _call_app: str = ""
    _joined_at: float = 0.0
    # Notebook of the call this tab starts, while its caller chooses the call app.
    _starting_notebook: str = ""

    async def _me(self) -> tuple[str, str, str]:
        """(notebook on screen, user id, display name) of this tab."""
        from cellora.states.notebook import NotebookState

        nb = await self.get_state(NotebookState)
        me = nb.me or {}
        return nb.nb_id, me.get("user", ""), me.get("name", "")

    async def _room(self, nb_id: str) -> None:
        """Pushes the call of ``nb_id`` to its room, when that is the notebook on screen."""
        on_screen, _, _ = await self._me()
        if nb_id and on_screen == nb_id:
            sync_room(await self.get_state(NotebookRoom), nb_id)

    @rx.event
    async def join_call(self):
        """Call everyone on the notebook, or join its call, and open it in the dialog."""
        return await self._join(CALL_APP or "")

    @rx.event
    async def start_call(self, data: dict):
        """The caller chose the call app: call everyone on the notebook with it."""
        nb_id, self._starting_notebook = self._starting_notebook, ""
        on_screen, _, _ = await self._me()
        if nb_id and nb_id == on_screen and data.get("app"):
            # Joins instead if someone called everyone on the notebook meanwhile.
            return await self._join(data["app"])

    async def _join(self, app: str):
        """Join the notebook's call in its app, or start the call in ``app``.

        Without ``app``, a new call first lets the caller choose it (``start_call`` comes back
        here with it).
        """
        if not CALLS_ENABLED:
            return
        nb_id, user_id, name = await self._me()
        if not nb_id or not user_id:
            return
        dialog = await self.get_state(IntentState)
        if self.current_notebook == nb_id and CALL_ACTION in dialog.open_actions:
            # Already in this call (maybe minimized): bring it back rather than rejoin.
            return Intent.show(CALL_ACTION)
        if nb_id not in CallStore.calls and not app:
            # Everyone who joins later opens the app chosen now: other apps can't reach the call.
            self._starting_notebook = nb_id
            return Intent.choose_app(CALL_ACTION, on_result=CallState.start_call)

        now = time.time()
        token = self.router.session.client_token
        if self.current_notebook and self.current_notebook != nb_id:
            # Switching calls: the old dialog's on_cancel comes too late to know which one.
            CallStore.leave(self.current_notebook, token)
        call = CallStore.join(nb_id, token, Member(id=user_id, name=name), now, app, invited=viewers.users_on(nb_id))
        self.current_notebook = nb_id
        self._call_id = call["call_id"]
        self._call_app = call["app"]
        self._joined_at = now
        await self._room(nb_id)

        room = await self.get_state(NotebookRoom)
        title = room.title or "Notebook"
        return Intent.start(
            call["app"],
            CALL_ACTION,
            on_result=CallState.call_closed,
            on_cancel=CallState.call_closed,
            private={"room": call["call_id"]},
            keep_alive=True,
            single=True,
            label=f"Call · {title}",
            title=title,
            user=user_id,
            name=name,
        )

    @rx.event
    async def decline_call(self):
        nb_id, user_id, _ = await self._me()
        CallStore.decline(nb_id, user_id)
        await self._room(nb_id)

    @rx.event
    async def call_closed(self, data: dict):
        """The call dialog closed (hung up or closed): leave the call.

        Other dialogs only minimize it (keep-alive), so being replaced means another call took
        its place (single): join_call already moved us.
        """
        if data.get("reason") == "replaced" and data.get("by") == CALL_ACTION:
            return
        nb_id = self.current_notebook
        self.current_notebook = ""
        self._call_id = ""
        if nb_id:
            CallStore.leave(nb_id, self.router.session.client_token)
            await self._room(nb_id)

    @rx.event
    async def heartbeat(self):
        """Keep this tab in its call while the call dialog runs (in front or minimized)."""
        if not CALLS_ENABLED or not self.current_notebook:
            return
        now = time.time()
        token = self.router.session.client_token
        nb_id = self.current_notebook
        dialog = await self.get_state(IntentState)
        if CALL_ACTION not in dialog.open_actions and now - self._joined_at > OPENING_GRACE_SECONDS:
            # The dialog went away without telling us: we are not in the call.
            CallStore.leave(nb_id, token)
            self.current_notebook = ""
            self._call_id = ""
            await self._room(nb_id)
            return

        CallStore.cleanup(nb_id, now)
        call = CallStore.calls.get(nb_id)
        if call is not None and call["call_id"] != self._call_id:
            # Another call took this notebook's place while ours was dropped.
            self.current_notebook = ""
            self._call_id = ""
            return
        if call is None or token not in call["members"] or now - call["last_seen"][token] > TOUCH_SECONDS:
            # Touch our entry, or put the call back if it was dropped (e.g. this tab slept past
            # STALE_SECONDS) while the dialog still runs it.
            _, user_id, name = await self._me()
            CallStore.join(nb_id, token, Member(id=user_id, name=name), now, self._call_app, call_id=self._call_id)
            if call is None:
                await self._room(nb_id)
