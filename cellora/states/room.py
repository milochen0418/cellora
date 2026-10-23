"""What everybody on a notebook sees around its cells, pushed to all of them at once.

``NotebookRoom`` is a Reflex ``SharedState``: every tab on /nb/<id> links its room to the
notebook (``room_token``). The cells themselves are not in it: they travel in the notebook's
shared document (:mod:`cellora.docs`), straight between the browsers and the server. The room
carries what the page draws with Reflex: the title, who is here, the kernel (starting, idle,
busy, how many cells wait), the variables, whether the notebook is saved, and its call.
"""

from __future__ import annotations

import dataclasses

import reflex as rx


def room_token(nb_id: str) -> str:
    """The shared-state token of a notebook (Reflex link tokens can't contain ``_``)."""
    return f"notebook-{nb_id}"


@dataclasses.dataclass
class Variable:
    """A row of the Variables panel."""

    name: str = ""
    type: str = ""
    size: str = ""
    preview: str = ""
    columns: list[str] = dataclasses.field(default_factory=list)


class NotebookRoom(rx.SharedState):
    title: str = ""
    # The notebook was deleted (here, or on re_ddns's storage page) while people had it open.
    gone: bool = False
    # "saved", "unsaved" or "error" (with the reason), and when it was last written.
    save_state: str = "saved"
    save_error: str = ""
    saved_at: float = 0.0

    # The kernel: off | starting | idle | busy | restarting | dead.
    kernel_state: str = "off"
    kernel_error: str = ""
    kernel_queued: int = 0
    kernel_running: str = ""
    kernel_started: float = 0.0
    variables: list[Variable] = []

    # One entry per tab on the notebook: {"id", "user", "name", "color", "avatar"}.
    peers: list[dict[str, str]] = []
    # The notebook's call: how many are in it, who is ringing everyone (while it rings), the
    # people it rings (those here when it started), and those who joined or declined.
    call_count: int = 0
    call_ringing_from: str = ""
    call_invited: list[str] = []
    call_responded: list[str] = []

    @rx.var
    def people(self) -> list[dict[str, str]]:
        """One entry per person: several tabs of one person count once."""
        out: list[dict[str, str]] = []
        seen = set()
        for p in self.peers:
            user = p.get("user") or p.get("id")
            if user in seen:
                continue
            seen.add(user)
            name = p.get("name", "") or "?"
            initials = "".join(w[0] for w in name.split()[:2]).upper() or "?"
            out.append({"id": p.get("id", ""), "user": user, "name": name, "initials": initials, "color": p.get("color", ""), "avatar": p.get("avatar", "")})
        return out

    @rx.var
    def people_count(self) -> int:
        return len({p.get("user") or p.get("id") for p in self.peers})

    @rx.var
    def kernel_busy(self) -> bool:
        return self.kernel_state in ("busy", "starting", "restarting") or self.kernel_queued > 0
