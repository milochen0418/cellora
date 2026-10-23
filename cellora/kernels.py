"""The kernels: one Python kernel per notebook, shared by everyone on it.

As in Databricks or Colab, a notebook has one kernel, and what anyone runs, everyone's code sees
(the variables, the imports). Runs wait in one queue per notebook and go one at a time: Shift+Enter
on a cell puts it in the queue (``queued``, with who asked), the kernel runs it (``running``, the
outputs reach everyone as they come), and the cell says how it went (``ok`` / ``error``, how long,
by whom). A failing cell cancels the cells queued after it, as "Run all" does in Jupyter.

The kernel is Jupyter's own (ipykernel, through jupyter_client), so ``%pip``, ``%timeit``,
matplotlib, ``display()`` and every Jupyter output work. It starts in the workspace
(:func:`cellora.paths.files_dir`), the folder the Files panel shows. It starts when a cell first
runs, and stops after ``IDLE_MINUTES`` without runs and without anyone on the notebook; at most
``MAX_KERNELS`` run at once (the one unused longest stops first).

Outputs and run status are written into the notebook's shared document (:mod:`cellora.docs`);
what the page's top bar and side panels show (the kernel's state, the queue, the variables) goes
to the notebook's room through ``on_change``.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import os
import re
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from cellora import paths
from cellora.docs import DOCS, LiveNotebook

log = logging.getLogger("cellora.kernels")

MAX_KERNELS = int(os.environ.get("CELLORA_MAX_KERNELS") or 6)
IDLE_MINUTES = float(os.environ.get("CELLORA_KERNEL_IDLE_MINUTES") or 60)
# Printed text is written into the notebook at most this often while a cell runs.
STREAM_FLUSH = 0.08
# How long completion and docs requests may wait for a busy kernel.
QUICK_TIMEOUT = 2.0
BOOT_CODE = (Path(__file__).with_name("kernel_boot.py")).read_text("utf-8")

# Settings the kernel's code must not see (the app's secrets), and the start of names like them.
_HIDDEN_ENV = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CELLORA_RUN_PASSCODE", "DDNS_AUTH_SECRET", "RE_DDNS_ADMIN_TOKEN", "ADMIN_PASSCODE"}
_SECRET_WORDS = re.compile(r"(SECRET|PASSCODE|PASSWORD|API_KEY|AUTH_TOKEN)", re.I)
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def kernel_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _HIDDEN_ENV and not _SECRET_WORDS.search(k) and not k.startswith("REFLEX_")}
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("PAGER", "cat")
    return env


@dataclass
class Run:
    cell_id: str
    by: dict
    queued_at: float = field(default_factory=time.time)


class _Probe:
    """Notices that the kernel's outputs channel carries messages again."""

    def __init__(self) -> None:
        self.seen = asyncio.Event()

    def handle(self, msg: dict) -> None:
        self.seen.set()


class Sink:
    """Where one execution's outputs go: into its cell, as Jupyter would show them."""

    def __init__(self, session: "KernelSession", live: LiveNotebook, cell_id: str):
        self.session = session
        self.live = live
        self.cell_id = cell_id
        self.pending: list[tuple[str, str]] = []
        self.flush_handle: Optional[asyncio.TimerHandle] = None
        self.clear_next = False
        self.idle = asyncio.Event()
        self.error: Optional[dict] = None

    def _clear_if_asked(self) -> None:
        if self.clear_next:
            self.clear_next = False
            self.live.clear_outputs(self.cell_id)

    def flush(self) -> None:
        if self.flush_handle is not None:
            self.flush_handle.cancel()
            self.flush_handle = None
        if not self.pending:
            return
        pending, self.pending = self.pending, []
        self._clear_if_asked()
        with self.live.doc.transaction():
            # Consecutive pieces of the same stream go in at once.
            name, text = pending[0]
            for n, t in pending[1:]:
                if n == name:
                    text += t
                else:
                    self.live.stream(self.cell_id, name, text)
                    name, text = n, t
            self.live.stream(self.cell_id, name, text)

    def handle(self, msg: dict) -> None:
        kind = msg["msg_type"]
        content = msg.get("content") or {}
        if kind == "stream":
            self.pending.append((content.get("name") or "stdout", content.get("text") or ""))
            if self.flush_handle is None:
                self.flush_handle = asyncio.get_running_loop().call_later(STREAM_FLUSH, self.flush)
        elif kind in ("display_data", "execute_result", "error"):
            self.flush()
            self._clear_if_asked()
            output = dict(content)
            output["output_type"] = kind
            index = self.live.add_output(self.cell_id, output)
            display_id = (content.get("transient") or {}).get("display_id")
            if display_id and index is not None:
                self.session.displays.setdefault(display_id, set()).add(self.cell_id)
            if kind == "error":
                self.error = content
        elif kind == "update_display_data":
            self.flush()
            self.session.update_display(content)
        elif kind == "clear_output":
            self.flush()
            if content.get("wait"):
                self.clear_next = True
            else:
                self.live.clear_outputs(self.cell_id)
        elif kind == "status" and content.get("execution_state") == "idle":
            self.flush()
            self.idle.set()


class KernelSession:
    """A notebook's kernel, its run queue and what it runs now."""

    def __init__(self, nb_id: str, on_change: Callable[[str], None]):
        self.nb_id = nb_id
        self.on_change = on_change
        self.km: Any = None
        self.kc: Any = None
        # off | starting | idle | busy | restarting | dead
        self.state = "off"
        self.error = ""
        self.queue: deque[Run] = deque()
        self.current: Optional[Run] = None
        self.started_at = 0.0
        self.last_used = time.time()
        self.variables: list[dict] = []
        self.displays: dict[str, set[str]] = {}
        self._worker: Optional[asyncio.Task] = None
        self._readers: list[asyncio.Task] = []
        self._replies: dict[str, asyncio.Future] = {}
        self._sinks: dict[str, Sink] = {}
        self._starting: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------ what the page shows

    def info(self) -> dict:
        return {
            "state": self.state,
            "error": self.error,
            "queued": len(self.queue),
            "running": self.current.cell_id if self.current else "",
            "started_at": self.started_at,
        }

    def _changed(self) -> None:
        try:
            self.on_change(self.nb_id)
        except Exception:
            log.exception("kernel on_change failed")

    @property
    def busy(self) -> bool:
        return self.current is not None or bool(self.queue) or self.state in ("starting", "restarting")

    # ------------------------------------------------------------ the process

    async def ensure_started(self) -> bool:
        if self.state in ("idle", "busy"):
            return True
        if self.state == "dead" and self.km is not None:
            return await self.restart()
        if self._starting is None or self._starting.done():
            self._starting = asyncio.create_task(self._start())
        try:
            return await asyncio.shield(self._starting)
        except Exception:
            return False

    async def _start(self) -> bool:
        from jupyter_client.manager import AsyncKernelManager

        self.state, self.error = "starting", ""
        self._changed()
        try:
            # Local sockets (no TCP ports), in a folder only this user can open.
            name = f"{self.nb_id}-{secrets.token_hex(3)}"
            km = AsyncKernelManager(kernel_name="python3", transport="ipc")
            km.connection_file = str(paths.runtime_dir() / f"kernel-{name}.json")
            km.ip = str(paths.socket_dir() / name)
            await km.start_kernel(cwd=str(paths.files_dir()), env=kernel_env())
            kc = km.client()
            kc.start_channels()
            await kc.wait_for_ready(timeout=120)
        except Exception as e:
            log.warning("kernel for %s didn't start: %s", self.nb_id, e)
            self.state, self.error = "dead", f"The kernel didn't start: {e}"
            self._changed()
            return False
        self.km, self.kc = km, kc
        self._readers = [asyncio.create_task(self._read_iopub()), asyncio.create_task(self._read_shell()), asyncio.create_task(self._watch())]
        await self._boot()
        self.started_at = time.time()
        self.state = "idle"
        self._changed()
        return True

    async def _boot(self) -> None:
        reply = await self._request(lambda: self.kc.execute(BOOT_CODE, silent=True, store_history=False), timeout=60)
        if reply is None or (reply.get("content") or {}).get("status") != "ok":
            log.warning("kernel for %s: boot code failed: %s", self.nb_id, (reply or {}).get("content"))

    async def _watch(self) -> None:
        """Notices a kernel that died (out of memory, os._exit...)."""
        while self.km is not None:
            await asyncio.sleep(2.0)
            if self.state in ("restarting", "starting"):
                continue
            try:
                alive = await self.km.is_alive()
            except Exception:
                alive = False
            if not alive and self.km is not None:
                self._died("The kernel stopped (it may have run out of memory). Restart it to go on.")
                return

    def _died(self, why: str) -> None:
        self.state, self.error = "dead", why
        for sink in list(self._sinks.values()):
            if isinstance(sink, Sink):
                sink.idle.set()
        for fut in list(self._replies.values()):
            if not fut.done():
                fut.set_result(None)
        self._cancel_queue("cancelled")
        self._changed()

    async def shutdown(self) -> None:
        self._cancel_queue("cancelled")
        for t in self._readers:
            t.cancel()
        self._readers = []
        km, kc = self.km, self.kc
        self.km = self.kc = None
        if kc is not None:
            try:
                kc.stop_channels()
            except Exception:
                pass
        if km is not None:
            try:
                await km.shutdown_kernel(now=False)
            except Exception:
                try:
                    await km.shutdown_kernel(now=True)
                except Exception:
                    pass
        self.state, self.error, self.variables = "off", "", []
        self.current = None
        self._changed()

    async def interrupt(self) -> None:
        self._cancel_queue("cancelled")
        if self.km is not None and self.current is not None:
            try:
                await self.km.interrupt_kernel()
            except Exception as e:
                log.warning("kernel for %s: interrupt failed: %s", self.nb_id, e)
        self._changed()

    async def restart(self) -> bool:
        if self.km is None:
            return await self.ensure_started()
        self._cancel_queue("cancelled")
        self.state = "restarting"
        self._changed()
        try:
            await self.km.restart_kernel(now=True)
            ready = await self._wait_ready(120)
        except Exception as e:
            ready, e_text = False, str(e)
        else:
            e_text = "it didn't answer"
        if not ready:
            self._died(f"The kernel didn't restart: {e_text}")
            return False
        for sink in list(self._sinks.values()):
            if isinstance(sink, Sink):
                sink.idle.set()
        for fut in list(self._replies.values()):
            if not fut.done():
                fut.set_result(None)
        if not any(not t.done() for t in self._readers[2:]):
            self._readers.append(asyncio.create_task(self._watch()))
        await self._boot()
        self.variables = []
        self.displays = {}
        self.started_at = time.time()
        self.state = "idle" if self.current is None else "busy"
        self._changed()
        return True

    # ------------------------------------------------------------ messages

    async def _read_iopub(self) -> None:
        while self.kc is not None:
            try:
                msg = await self.kc.get_iopub_msg()
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(0.05)
                continue
            parent = (msg.get("parent_header") or {}).get("msg_id")
            sink = self._sinks.get(parent)
            if sink is not None:
                try:
                    sink.handle(msg)
                except Exception:
                    log.exception("kernel for %s: output not written", self.nb_id)

    async def _read_shell(self) -> None:
        while self.kc is not None:
            try:
                msg = await self.kc.get_shell_msg()
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(0.05)
                continue
            parent = (msg.get("parent_header") or {}).get("msg_id")
            fut = self._replies.pop(parent, None)
            if fut is not None and not fut.done():
                fut.set_result(msg)

    async def _wait_ready(self, timeout: float) -> bool:
        """Asks the kernel who it is until it answers on both channels (jupyter_client's own
        wait_for_ready reads the channels itself, which the reader tasks already do). The
        outputs' channel reconnects a moment after the kernel restarted, and what the kernel
        says before that is lost: so it waits for the kernel's status message too."""
        end = time.time() + timeout
        while time.time() < end:
            probe = _Probe()
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            msg_id = self.kc.kernel_info()
            self._replies[msg_id] = fut
            self._sinks[msg_id] = probe
            try:
                await asyncio.wait_for(fut, 1.0)
                await asyncio.wait_for(probe.seen.wait(), 0.5)
                return True
            except asyncio.TimeoutError:
                pass
            finally:
                self._replies.pop(msg_id, None)
                self._sinks.pop(msg_id, None)
            if not await self.km.is_alive():
                return False
        return False

    async def _request(self, send: Callable[[], str], timeout: Optional[float] = None) -> Optional[dict]:
        """Sends a shell request and waits for its reply (None on timeout or when the kernel
        went away)."""
        if self.kc is None:
            return None
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        msg_id = send()
        self._replies[msg_id] = fut
        try:
            return await asyncio.wait_for(fut, timeout) if timeout else await fut
        except asyncio.TimeoutError:
            self._replies.pop(msg_id, None)
            return None

    # ------------------------------------------------------------ running cells

    def enqueue(self, cell_ids: list[str], by: dict) -> int:
        """Puts code cells in the queue (those not waiting or running already)."""
        live = DOCS.get(self.nb_id)
        if live is None:
            return 0
        waiting = {r.cell_id for r in self.queue} | ({self.current.cell_id} if self.current else set())
        added = 0
        now = time.time()
        with live.doc.transaction():
            for cell_id in cell_ids:
                if cell_id in waiting or live.cell_type(cell_id) != "code":
                    continue
                self.queue.append(Run(cell_id, by, now))
                waiting.add(cell_id)
                live.set_run(cell_id, state="queued", queued_at=now, by=by.get("name", ""), color=by.get("color", ""))
                added += 1
        self.last_used = now
        if added and (self._worker is None or self._worker.done()):
            self._worker = asyncio.create_task(self._work())
        self._changed()
        return added

    def _cancel_queue(self, state: str) -> None:
        live = DOCS.get(self.nb_id)
        queued, self.queue = list(self.queue), deque()
        if live is not None and queued:
            with live.doc.transaction():
                for run in queued:
                    live.set_run(run.cell_id, state=state, ended=time.time())

    async def _work(self) -> None:
        while self.queue:
            if not await self.ensure_started():
                self._cancel_queue("cancelled")
                break
            run = self.queue.popleft()
            live = await DOCS.open(self.nb_id)
            if live is None:
                self.queue.clear()
                break
            code = live.source(run.cell_id)
            if code is None:
                continue
            self.current = run
            self.state = "busy"
            self._changed()
            try:
                ok = await self._execute(live, run, code)
            except Exception as e:
                log.exception("kernel for %s: run failed", self.nb_id)
                live.set_run(run.cell_id, state="error", ended=time.time())
                self.error = str(e)
                ok = False
            self.current = None
            self.last_used = time.time()
            if not ok:
                self._cancel_queue("cancelled")
            if self.state == "busy":
                self.state = "idle"
            self._changed()
        if self.state in ("idle", "busy"):
            self.state = "idle"
            await self.refresh_variables()
            self._changed()

    async def _execute(self, live: LiveNotebook, run: Run, code: str) -> bool:
        started = time.time()
        live.last_editor = run.by.get("name", "") or live.last_editor
        with live.doc.transaction():
            live.clear_outputs(run.cell_id)
            live.set_execution_count(run.cell_id, None)
            live.set_run(run.cell_id, state="running", started=started, ended=None, duration=None, by=run.by.get("name", ""), color=run.by.get("color", ""))
        if not code.strip():
            live.set_run(run.cell_id, state="ok", ended=time.time(), duration=0.0)
            return True
        sink = Sink(self, live, run.cell_id)
        msg_id = self.kc.execute(code, silent=False, store_history=True, allow_stdin=False, stop_on_error=False)
        self._sinks[msg_id] = sink
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._replies[msg_id] = fut
        try:
            reply = await fut
            # The reply can come before the last outputs: wait for the kernel to go idle.
            try:
                await asyncio.wait_for(sink.idle.wait(), 5.0)
            except asyncio.TimeoutError:
                pass
        finally:
            sink.flush()
            self._sinks.pop(msg_id, None)
            self._replies.pop(msg_id, None)
        content = (reply or {}).get("content") or {}
        status = content.get("status") or ("error" if sink.error else "ok")
        count = content.get("execution_count")
        ended = time.time()
        with live.doc.transaction():
            for payload in content.get("payload") or []:
                if payload.get("source") == "page":
                    data = payload.get("data") or {}
                    live.add_output(run.cell_id, {"output_type": "display_data", "data": data, "metadata": {}})
            if isinstance(count, int):
                live.set_execution_count(run.cell_id, count)
            state = "ok" if status == "ok" else "error"
            if reply is None:
                state = "error"
                # The kernel went away mid-run (killed, out of memory, os._exit): say so here.
                live.add_output(run.cell_id, {
                    "output_type": "error",
                    "ename": "KernelStopped",
                    "evalue": self.error or "The kernel stopped while this cell ran. Restart it to go on.",
                    "traceback": [],
                })
            live.set_run(run.cell_id, state=state, ended=ended, duration=round(ended - started, 3), count=count if isinstance(count, int) else None)
        return state == "ok"

    def update_display(self, content: dict) -> None:
        """``update_display_data``: every output shown with that display id changes."""
        display_id = (content.get("transient") or {}).get("display_id")
        if not display_id:
            return
        live = DOCS.get(self.nb_id)
        if live is None:
            return
        for cell_id in self.displays.get(display_id, set()):
            outs = live.outputs(cell_id)
            if outs is None:
                continue
            for i, o in enumerate(list(outs)):
                if o.get("display_id") == display_id:
                    output = dict(content)
                    output["output_type"] = o.get("output_type") or "display_data"
                    if output["output_type"] == "execute_result":
                        output["execution_count"] = o.get("execution_count")
                    live.replace_output(cell_id, i, output)

    # ------------------------------------------------------------ quick questions

    async def refresh_variables(self) -> None:
        text = await self.expression("__cellora_vars()")
        if text is None:
            return
        try:
            self.variables = json.loads(text)
        except ValueError:
            self.variables = []

    async def expression(self, expr: str, timeout: float = 5.0) -> Optional[str]:
        """The value of a Python expression in the kernel, as a string (its str() when it is
        one); None when the kernel can't answer now."""
        if self.kc is None or self.state not in ("idle", "busy"):
            return None
        reply = await self._request(
            lambda: self.kc.execute("", silent=True, store_history=False, user_expressions={"v": expr}),
            timeout=timeout,
        )
        value = (((reply or {}).get("content") or {}).get("user_expressions") or {}).get("v") or {}
        if value.get("status") != "ok":
            return None
        text = (value.get("data") or {}).get("text/plain", "")
        try:
            out = ast.literal_eval(text)
            return out if isinstance(out, str) else text
        except (ValueError, SyntaxError):
            return text

    async def complete(self, code: str, cursor: int) -> dict:
        """Jupyter's completions at ``cursor`` (a position in characters)."""
        if self.kc is None or self.state not in ("idle", "busy"):
            return {"matches": [], "cursor_start": cursor, "cursor_end": cursor, "types": []}
        reply = await self._request(lambda: self.kc.complete(code, cursor), timeout=QUICK_TIMEOUT)
        content = (reply or {}).get("content") or {}
        types = (content.get("metadata") or {}).get("_jupyter_types_experimental") or []
        return {
            "matches": list(content.get("matches") or [])[:200],
            "cursor_start": int(content.get("cursor_start", cursor)),
            "cursor_end": int(content.get("cursor_end", cursor)),
            "types": [{"text": t.get("text", ""), "type": t.get("type", ""), "signature": t.get("signature", "")} for t in types[:200]],
        }

    async def inspect(self, code: str, cursor: int) -> str:
        """The docs of the name at ``cursor`` (plain text), or ""."""
        if self.kc is None or self.state not in ("idle", "busy"):
            return ""
        reply = await self._request(lambda: self.kc.inspect(code, cursor, detail_level=0), timeout=QUICK_TIMEOUT)
        content = (reply or {}).get("content") or {}
        if not content.get("found"):
            return ""
        return _ANSI.sub("", (content.get("data") or {}).get("text/plain", ""))


class Kernels:
    """Every notebook's kernel in this process."""

    def __init__(self) -> None:
        self.sessions: dict[str, KernelSession] = {}
        self.on_change: Optional[Callable[[str], None]] = None

    def _changed(self, nb_id: str) -> None:
        if self.on_change is not None:
            self.on_change(nb_id)

    def get(self, nb_id: str) -> Optional[KernelSession]:
        return self.sessions.get(nb_id)

    def session(self, nb_id: str) -> KernelSession:
        s = self.sessions.get(nb_id)
        if s is None:
            s = self.sessions[nb_id] = KernelSession(nb_id, self._changed)
        return s

    def info(self, nb_id: str) -> dict:
        s = self.sessions.get(nb_id)
        return s.info() if s is not None else {"state": "off", "error": "", "queued": 0, "running": "", "started_at": 0.0}

    def busy(self, nb_id: str) -> bool:
        s = self.sessions.get(nb_id)
        return s is not None and s.busy

    async def run(self, nb_id: str, cell_ids: list[str], by: dict) -> int:
        s = self.session(nb_id)
        added = s.enqueue(cell_ids, by)
        if added:
            await self._make_room(keep=nb_id)
        return added

    async def _make_room(self, keep: str) -> None:
        """At most MAX_KERNELS run: the idle one unused longest stops."""
        running = [s for s in self.sessions.values() if s.km is not None or s.state == "starting"]
        if len(running) <= MAX_KERNELS:
            return
        idle = sorted((s for s in running if s.nb_id != keep and not s.busy), key=lambda s: s.last_used)
        for s in idle[: len(running) - MAX_KERNELS]:
            log.info("stopping the kernel of %s to make room", s.nb_id)
            await s.shutdown()
            s.error = "The kernel was stopped to make room for others. Run a cell to start it again."
            self._changed(s.nb_id)

    async def stop_idle(self, people_on: Callable[[str], int], now: Optional[float] = None) -> list[str]:
        """Stops the kernels nobody used for IDLE_MINUTES and nobody has open."""
        now = time.time() if now is None else now
        stopped = []
        for nb_id, s in list(self.sessions.items()):
            if s.km is None or s.busy or people_on(nb_id):
                continue
            if now - s.last_used >= IDLE_MINUTES * 60:
                await s.shutdown()
                stopped.append(nb_id)
        return stopped

    async def shutdown_all(self) -> None:
        for s in list(self.sessions.values()):
            if s.km is not None:
                await s.shutdown()

    async def forget(self, nb_id: str) -> None:
        """The notebook was deleted: its kernel goes too."""
        s = self.sessions.pop(nb_id, None)
        if s is not None and s.km is not None:
            await s.shutdown()


KERNELS = Kernels()
