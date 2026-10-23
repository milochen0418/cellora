"""The notebook list (/): every notebook, who is on it now, and making new ones (blank, the
getting-started tour, written by the AI from a request, or an .ipynb from your computer)."""

from __future__ import annotations

import asyncio
import dataclasses
import time

import reflex as rx

from cellora import ai, nbfile, templates, viewers
from cellora.docs import DOCS
from cellora.kernels import KERNELS
from cellora.states.lang import text

MAX_UPLOAD = 50 * 1024 * 1024


@dataclasses.dataclass
class Person:
    name: str = ""
    initials: str = ""
    color: str = ""
    avatar: str = ""


@dataclasses.dataclass
class NotebookRow:
    id: str = ""
    title: str = ""
    updated: float = 0.0
    by: str = ""
    cells: int = 0
    people: list[Person] = dataclasses.field(default_factory=list)
    kernel: str = ""
    ago: str = ""
    cells_text: str = ""


def ago(seconds: float, lang: str) -> str:
    """How long ago, in words ("5 min ago", "5 分鐘前")."""
    from cellora.i18n import TEXTS

    t = TEXTS.get(lang, TEXTS["en"])
    s = max(0, int(seconds))
    if s < 45:
        return t["ago_now"]
    for size, key in ((86400 * 30, "ago_months"), (86400, "ago_days"), (3600, "ago_hours"), (60, "ago_minutes")):
        if s >= size:
            return t[key].format(n=s // size)
    return t["ago_minutes"].format(n=1)


def cells_text(n: int, lang: str) -> str:
    from cellora.i18n import TEXTS

    t = TEXTS.get(lang, TEXTS["en"])
    return (t["cells_one"] if n == 1 else t["cells_many"]).format(n=n)


class HomeState(rx.State):
    rows: list[NotebookRow] = []
    query: str = ""
    loaded: bool = False
    # The new-notebook dialog.
    new_open: bool = False
    new_title: str = ""
    ai_request: str = ""
    ai_busy: bool = False
    ai_progress: str = ""
    ai_error: str = ""
    # Asking before a delete.
    confirm_id: str = ""
    confirm_title: str = ""
    upload_error: str = ""
    now: float = 0.0

    @rx.var
    def shown(self) -> list[NotebookRow]:
        q = self.query.strip().lower()
        return [r for r in self.rows if q in r.title.lower() or q in r.by.lower()] if q else list(self.rows)

    @rx.event
    async def refresh(self):
        from cellora.states.lang import LangState

        lang = (await self.get_state(LangState)).lang
        metas = DOCS.with_open(await asyncio.to_thread(DOCS.store.list))
        watching = viewers.watching()
        now = time.time()
        rows = []
        for m in metas:
            people = [
                Person(name=p["name"], initials=("".join(w[0] for w in (p["name"] or "?").split()[:2]).upper() or "?"), color=p["color"], avatar=p.get("avatar", ""))
                for p in watching.get(m.id, [])
            ]
            rows.append(NotebookRow(id=m.id, title=m.title, updated=m.updated_at, by=m.last_editor or m.created_by_name, cells=m.cells, people=people, kernel=KERNELS.info(m.id)["state"], ago=ago(now - m.updated_at, lang), cells_text=cells_text(m.cells, lang)))
        self.rows = rows
        self.now = time.time()
        self.loaded = True

    @rx.event
    def set_query(self, value: str):
        self.query = value

    # ------------------------------------------------------------ new notebooks

    @rx.event
    def open_new(self):
        self.new_open, self.new_title, self.ai_request, self.ai_error, self.ai_progress = True, "", "", "", ""

    @rx.event
    def set_new_open(self, value: bool):
        if not self.ai_busy:
            self.new_open = value

    @rx.event
    def set_new_title(self, value: str):
        self.new_title = value

    @rx.event
    def set_ai_request(self, value: str):
        self.ai_request = value

    async def _creator(self) -> dict:
        from cellora.states.notebook import NotebookState

        nb = await self.get_state(NotebookState)
        await nb._identify()
        return {"name": nb.me.get("name", ""), "email": nb.me.get("email", "")}

    @rx.event
    async def create_blank(self):
        creator = await self._creator()
        title = self.new_title.strip() or await text(self, "untitled")
        nb = nbfile.new_notebook(title)
        meta = await asyncio.to_thread(DOCS.create, nb, title, creator)
        self.new_open = False
        return rx.redirect(f"/nb/{meta.id}")

    @rx.event
    async def create_tour(self):
        from cellora.states.lang import LangState

        creator = await self._creator()
        lang = (await self.get_state(LangState)).lang
        nb = templates.tour(lang)
        meta = await asyncio.to_thread(DOCS.create, nb, nbfile.title_of(nb), creator)
        self.new_open = False
        return rx.redirect(f"/nb/{meta.id}")

    @rx.event(background=True)
    async def create_with_ai(self):
        async with self:
            request = self.ai_request.strip()
            if not request or self.ai_busy:
                return
            self.ai_busy, self.ai_error = True, ""
            self.ai_progress = await text(self, "ai_writing_notebook")
            creator = await self._creator()
            title = self.new_title.strip()
        answer = ""
        try:
            shown = 0.0
            async for piece in ai.stream_notebook(request):
                answer += piece
                if time.time() - shown > 0.3:
                    async with self:
                        self.ai_progress = await text(self, "ai_writing_progress", n=str(answer.count("```") // 2))
                    shown = time.time()
            cells = nbfile.cells_from_markdown(answer)
            if not cells:
                raise ai.AIUnavailable("The answer had no cells.")
            nb = nbfile.new_notebook(title or "AI notebook", cells=cells)
            if not title:
                nb.metadata["cellora"] = {}
                title = nbfile.title_of(nb, request[:60])
            meta = await asyncio.to_thread(DOCS.create, nb, title, creator)
        except Exception as e:
            async with self:
                self.ai_busy, self.ai_progress = False, ""
                self.ai_error = str(e) or type(e).__name__
            return
        async with self:
            self.ai_busy, self.new_open, self.ai_progress = False, False, ""
            return rx.redirect(f"/nb/{meta.id}")

    @rx.event
    async def upload(self, files: list[rx.UploadFile]):
        self.upload_error = ""
        creator = await self._creator()
        made = []
        for f in files:
            data = bytearray()
            while chunk := await f.read(1024 * 1024):
                data.extend(chunk)
                if len(data) > MAX_UPLOAD:
                    self.upload_error = await text(self, "upload_too_big", name=f.name or "")
                    return
            try:
                nb = nbfile.parse(bytes(data))
            except ValueError:
                self.upload_error = await text(self, "upload_not_notebook", name=f.name or "")
                continue
            name = (f.name or "Notebook").rsplit("/", 1)[-1]
            title = nbfile.title_of(nb, name[:-6] if name.lower().endswith(".ipynb") else name)
            made.append(await asyncio.to_thread(DOCS.create, nb, title, creator))
        if len(made) == 1:
            return rx.redirect(f"/nb/{made[0].id}")
        await self.refresh()

    @rx.event
    async def duplicate(self, nb_id: str):
        data = DOCS.file_bytes(nb_id)
        meta = DOCS.meta(nb_id)
        if data is None or meta is None:
            return
        nb = nbfile.parse(data)
        title = await text(self, "copy_of", title=meta.title)
        await asyncio.to_thread(DOCS.create, nb, title, await self._creator())
        await self.refresh()

    # ------------------------------------------------------------ deleting

    @rx.event
    def ask_delete(self, nb_id: str, title: str):
        self.confirm_id, self.confirm_title = nb_id, title

    @rx.event
    def cancel_delete(self):
        self.confirm_id = ""

    @rx.event
    async def delete(self):
        nb_id, self.confirm_id = self.confirm_id, ""
        if not nb_id:
            return
        await KERNELS.forget(nb_id)
        DOCS.delete(nb_id)
        from cellora import rooms

        rooms.push_later(nb_id)
        await self.refresh()
