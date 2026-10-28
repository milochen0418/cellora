"""One tab's AI: the assistant panel's conversation, and the cell change the AI is writing.

Each person has their own conversation and drafts (as in Databricks Assistant); what they accept
lands in the shared notebook, where everyone sees it. The answers stream in from
:mod:`cellora.ai`, which knows the notebook as it is when the question is asked.
"""

from __future__ import annotations

import dataclasses
import json
import re
import time

import reflex as rx

from cellora import ai, context
from cellora.docs import DOCS
from cellora.kernels import KERNELS
from cellora.states.lang import text

# How often a streaming answer is shown as it grows.
STREAM_EVERY = 0.08
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


@dataclasses.dataclass
class ChatPart:
    kind: str = "text"  # "text" (Markdown) or "code"
    text: str = ""
    lang: str = ""


@dataclasses.dataclass
class ChatMessage:
    role: str = "user"  # "user" or "assistant"
    text: str = ""
    parts: list[ChatPart] = dataclasses.field(default_factory=list)
    # "" when done, "streaming" while it comes, "error" (text says why).
    status: str = ""


class AIState(rx.State):
    messages: list[ChatMessage] = []
    question: str = ""
    busy: bool = False
    # Which AI answers: {"provider": "claude" | "ollama" | "", "model", "detail"}.
    info: dict[str, str] = {}
    # The change the AI writes for a cell, shown there as a diff:
    # {"cell", "text", "status": "streaming" | "done" | "error", "error", "kind", "seq"}.
    draft: dict[str, str] = {}
    _job: int = 0
    _draft_job: int = 0

    @rx.var
    def has_ai(self) -> bool:
        return bool(self.info.get("provider"))

    @rx.event
    async def load(self):
        self.info = {k: str(v) for k, v in (await ai.status()).items()}

    @rx.event
    async def recheck(self):
        self.info = {k: str(v) for k, v in (await ai.status(refresh=True)).items()}

    @rx.event
    def set_question(self, value: str):
        self.question = value

    @rx.event
    def clear_chat(self):
        self._job += 1
        self.messages, self.busy = [], False

    @rx.event
    def stop(self):
        self._job += 1
        self.busy = False
        if self.messages and self.messages[-1].status == "streaming":
            last = self.messages[-1]
            self.messages = [*self.messages[:-1], dataclasses.replace(last, status="")]

    async def _where(self) -> tuple[str, str]:
        from cellora.states.notebook import NotebookState

        nb = await self.get_state(NotebookState)
        return nb.nb_id, nb.focus_cell

    async def _who(self) -> str:
        """The person asking: their account (email) when signed in, else their guest name, for
        the AI Hub's log."""
        from cellora.states.notebook import NotebookState

        me = (await self.get_state(NotebookState)).me or {}
        return str(me.get("email") or me.get("name") or me.get("user") or "")

    @staticmethod
    async def _context(nb_id: str, focus: str) -> str:
        live = DOCS.get(nb_id) or await DOCS.open(nb_id)
        if live is None:
            return ""
        session = KERNELS.get(nb_id)
        info = await ai.status()
        # The size budget follows the kind of model: the AI Hub says which kind answers.
        return context.describe(live, session.variables if session else [], focus, info.get("kind") or info.get("provider", ""))

    def _set_last(self, answer: str, status: str) -> None:
        if not self.messages:
            return
        last = self.messages[-1]
        parts = [ChatPart(**p) for p in ai.split_answer(answer)]
        self.messages = [*self.messages[:-1], dataclasses.replace(last, text=answer, parts=parts, status=status)]

    @rx.event
    def ask_about(self, question: str):
        """A ready-made question (the panel's suggestions)."""
        self.question = question
        return AIState.ask

    @rx.event
    async def explain(self, cell_id: str):
        nb_id, _ = await self._where()
        live = DOCS.get(nb_id)
        number = live.index_of(cell_id) + 1 if live is not None else 0
        self.question = await text(self, "ai_q_explain_cell", n=str(number))
        return AIState.ask

    @rx.event(background=True)
    async def ask(self):
        async with self:
            question = self.question.strip()
            if not question or self.busy:
                return
            self.question = ""
            self.busy = True
            history = [{"role": m.role, "content": m.text} for m in self.messages if m.text and m.status != "error"]
            self.messages = [
                *self.messages,
                ChatMessage(role="user", text=question, parts=[ChatPart(kind="text", text=question)]),
                ChatMessage(role="assistant", status="streaming"),
            ]
            nb_id, focus = await self._where()
            user = await self._who()
            self._job += 1
            job = self._job
        # The box keeps its own text (see the panel), so it is emptied here, not through question.
        yield rx.set_value("ai-input", "")
        answer = ""
        try:
            notebook = await self._context(nb_id, focus)
            shown = 0.0
            async for piece in ai.stream_chat(history, question, notebook, user=user):
                answer += piece
                if time.time() - shown >= STREAM_EVERY:
                    async with self:
                        if self._job != job:
                            return
                        self._set_last(answer, "streaming")
                    shown = time.time()
            async with self:
                if self._job == job:
                    self._set_last(answer or "…", "")
                    self.busy = False
        except Exception as e:
            async with self:
                if self._job == job:
                    self._set_last(f"{answer}\n\n{_reason(e)}" if answer else _reason(e), "error")
                    self.busy = False

    # ------------------------------------------------------------ cell drafts

    @rx.event(background=True)
    async def start_edit(self, cell_id: str, instruction: str, kind: str):
        """Writes a new version of a cell: from an instruction (kind "edit") or to fix the error
        it raised ("fix")."""
        async with self:
            nb_id, _ = await self._where()
            user = await self._who()
            self._draft_job += 1
            job = self._draft_job
            self.draft = {"cell": cell_id, "text": "", "status": "streaming", "error": "", "kind": kind, "seq": str(job)}
            if kind == "fix":
                instruction = await text(self, "ai_fix_instruction")
        live = DOCS.get(nb_id) or await DOCS.open(nb_id)
        if live is None or live.cell(cell_id) is None:
            return
        source = live.source(cell_id) or ""
        cell_type = live.cell_type(cell_id)
        error = _last_error(live, cell_id) if kind == "fix" else ""
        answer = ""
        try:
            notebook = await self._context(nb_id, cell_id)
            shown = 0.0
            async for piece in ai.stream_edit(instruction or "Write this cell.", source, cell_type, notebook, error, user=user):
                answer += piece
                if time.time() - shown >= STREAM_EVERY:
                    async with self:
                        if self._draft_job != job:
                            return
                        self.draft = {**self.draft, "text": ai.code_of(answer)}
                    shown = time.time()
            async with self:
                if self._draft_job == job:
                    code = ai.code_of(answer)
                    if code.strip():
                        self.draft = {**self.draft, "text": code, "status": "done"}
                    else:
                        self.draft = {**self.draft, "status": "error", "error": await text(self, "ai_no_answer")}
        except Exception as e:
            async with self:
                if self._draft_job == job:
                    self.draft = {**self.draft, "status": "error", "error": _reason(e)}

    @rx.event
    def clear_draft(self):
        self._draft_job += 1
        self.draft = {}

    # ------------------------------------------------------------ code from the answers

    async def _insert(self, code: str, lang: str) -> str:
        from cellora.states.notebook import NotebookState

        nb = await self.get_state(NotebookState)
        live = DOCS.get(nb.nb_id)
        if live is None:
            return ""
        source = f"%%sql\n{code}" if lang == "sql" and not code.lstrip().startswith("%%sql") else code
        index = live.index_of(nb.focus_cell)
        cell_type = "markdown" if lang in ("markdown", "md") else "code"
        new = live.insert_cell(index + 1 if index >= 0 else len(live.cells), cell_type, source)
        nb.focus_cell = new
        nb._command("scroll", cell=new)
        return new

    @rx.event
    async def insert_code(self, code: str, lang: str):
        await self._insert(code, lang)

    @rx.event
    async def run_code(self, code: str, lang: str):
        from cellora.states.notebook import NotebookState

        new = await self._insert(code, lang)
        if new:
            nb = await self.get_state(NotebookState)
            return await nb._run([new])


def _last_error(live, cell_id: str) -> str:
    """The error a cell raised, as text (the last lines of its traceback)."""
    for o in reversed(live.to_ipynb()["cells"][live.index_of(cell_id)].get("outputs", [])):
        if o.get("output_type") == "error":
            tb = [_ANSI.sub("", line) for line in o.get("traceback") or []]
            return f"{o.get('ename')}: {o.get('evalue')}\n" + "\n".join(tb[-12:])
    return ""


def _reason(e: Exception) -> str:
    name = type(e).__name__
    if isinstance(e, ai.AIUnavailable):
        return f"⚠️ {e}"
    detail = str(e) or name
    try:
        body = json.loads(getattr(getattr(e, "response", None), "text", "") or "{}")
        detail = body.get("error", {}).get("message", detail)
    except Exception:
        pass
    return f"⚠️ {name}: {detail}"
