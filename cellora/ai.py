"""The AI that helps in a notebook: the AI Hub's models on a re_ddns host, else Claude when the
app has an Anthropic API key, else a local model through Ollama, else none (the notebook works
the same without it).

What it does (the prompts are below):

* ``inline``: the grey suggestion while you type in a code cell (Tab takes it), from the code
  before and after the cursor and the notebook's other cells. Local models fill in the middle
  (FIM); Claude is asked for the text to insert.
* ``stream_edit``: writes or rewrites one cell from an instruction (⌘I), or fixes the error it
  raised; the notebook shows the change as a diff to accept or reject.
* ``stream_chat``: the assistant panel's answers, which know the notebook (cells, outputs,
  variables).

On a re_ddns host with the AI Hub, the hub's models answer (``reflex_ddns_auth.ai``): the
notebook asks for a role (``smart`` for the chat, ``coder`` for cells and new notebooks,
``fast`` for suggestions), and which model answers each role is set on the hub's page, for every
app at once; the app holds no API key. Without a hub, Claude or Ollama as above.

Settings (environment): ``CELLORA_AI_PROVIDER`` = auto (default) | hub | claude | ollama | off;
``ANTHROPIC_API_KEY`` and ``CELLORA_CLAUDE_MODEL`` (default claude-opus-5-5); ``OLLAMA_URL``
(default: the Docker host's Ollama in a container, localhost otherwise) and ``OLLAMA_MODEL``
(default: a pulled model with "coder" in its name, else the first one); ``DDNS_AI_HUB_URL``
names the hub outside the platform (on it, the hub is found through re-ddns).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import AsyncIterator, Optional

import httpx

try:
    from reflex_ddns_auth import ai as hub  # the AI Hub's models, on a re_ddns host
except ImportError:  # the app runs without reflex_ddns_auth
    hub = None

log = logging.getLogger("cellora.ai")

PROVIDER = (os.environ.get("CELLORA_AI_PROVIDER") or "auto").strip().lower()
CLAUDE_MODEL = os.environ.get("CELLORA_CLAUDE_MODEL") or "claude-opus-5-5"
# What the notebook asks the AI Hub for; which model answers each role is set on the hub's page.
HUB_ROLES = {"chat": "smart", "edit": "coder", "notebook": "coder", "inline": "fast"}
# Claude routes a declined request to Anthropic's recommended fallback model by itself.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _default_ollama_url() -> str:
    in_docker = os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")
    return "http://host.docker.internal:11434" if in_docker else "http://localhost:11434"


OLLAMA_URL = (os.environ.get("OLLAMA_URL") or _default_ollama_url()).rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL") or ""

SYSTEM_CHAT = """You are the AI assistant of Cellora, a Python notebook that several people use together (Jupyter-compatible .ipynb, one shared IPython kernel).
You help the person asking to write, fix, explain and improve the notebook's code and analysis. Below the question you get the notebook as it is now: its cells in order (numbered, with their code and a summary of their outputs), the cell the person is on, and the variables in the kernel.

- Code meant to be run in the notebook goes in ```python fenced blocks, one block per cell, each runnable on its own after the cells above it. SQL for the notebook's %%sql magic (DuckDB over the notebook's DataFrames by variable name, and over files like 'sales.csv') goes in ```sql blocks.
- pandas, numpy, matplotlib and duckdb are installed; anything else needs %pip install in a cell.
- Refer to cells by their number. Use the variables and columns that exist; don't invent data.
- Be concise: short explanations, then the code. Answer in the language the person writes in."""

SYSTEM_EDIT = """You edit one cell of a Python notebook (Jupyter-compatible, one shared IPython kernel; pandas, numpy, matplotlib and duckdb are installed; %%sql runs DuckDB SQL over the notebook's DataFrames).
You get the notebook as it is now, the target cell's current content, and what to do with it.
Reply with only the complete new content of the target cell, in one fenced block (```python, or ```markdown for a Markdown cell). No explanation before or after it. Keep what should stay; change what the instruction asks. Use the variables and columns that exist in the notebook. Comments in the code may be in the language of the instruction."""

SYSTEM_INLINE = """You are a code completion engine inside a Python notebook cell.
You get the notebook's earlier cells, then the current cell with the cursor marked <CURSOR>.
Reply with only the text to insert at the cursor: the rest of the current line, and at most a few more lines when the code clearly continues. No fences, no explanation, nothing that is already after the cursor. Reply with nothing when no completion is useful."""

SYSTEM_NOTEBOOK = """You write a new Python notebook (Jupyter-compatible; pandas, numpy, matplotlib and duckdb are installed; %%sql runs DuckDB SQL over the notebook's DataFrames by variable name).
Reply in Markdown: a # title, short explanations between the steps, and each code cell as a ```python fenced block, runnable from top to bottom. When it needs data and none was given, make a small realistic sample in the first code cell. Keep it to what was asked; write the prose in the language of the request."""


class AIUnavailable(Exception):
    pass


_status_cache: tuple[float, dict] = (0.0, {})
_status_lock = asyncio.Lock()


def _claude_key() -> str:
    return os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN") or ""


async def status(refresh: bool = False) -> dict:
    """Which AI the app uses now: {"provider": claude|ollama|"", "model", "detail"}."""
    global _status_cache
    now = time.time()
    if not refresh and now - _status_cache[0] < 30 and _status_cache[1]:
        return _status_cache[1]
    async with _status_lock:
        if not refresh and time.time() - _status_cache[0] < 30 and _status_cache[1]:
            return _status_cache[1]
        info = await _find_provider()
        _status_cache = (time.time(), info)
        return info


async def _find_provider() -> dict:
    if PROVIDER == "off":
        return {"provider": "", "model": "", "detail": "AI is turned off (CELLORA_AI_PROVIDER=off)."}
    if PROVIDER in ("auto", "hub") and hub is not None:
        found = await hub.status(HUB_ROLES["chat"])
        if found and found.get("ok"):
            model = str(found.get("model") or "")
            # "kind": what sort of model answers (anthropic, ollama, openai), for the context budget.
            return {"provider": "hub", "model": model, "kind": str(found.get("provider") or ""), "detail": f"The AI Hub's {model}"}
        if PROVIDER == "hub":
            why = (found or {}).get("error") or "there is no AI Hub here (outside the platform, set DDNS_AI_HUB_URL)"
            return {"provider": "", "model": "", "detail": f"CELLORA_AI_PROVIDER is hub, but {why}"}
    if PROVIDER in ("auto", "claude") and _claude_key():
        return {"provider": "claude", "model": CLAUDE_MODEL, "detail": f"Claude ({CLAUDE_MODEL})"}
    if PROVIDER == "claude":
        return {"provider": "", "model": "", "detail": "CELLORA_AI_PROVIDER is claude, but there is no ANTHROPIC_API_KEY."}
    model = await _ollama_model()
    if model:
        return {"provider": "ollama", "model": model, "detail": f"{model} on Ollama ({OLLAMA_URL})"}
    return {
        "provider": "",
        "model": "",
        "detail": f"No AI found: set ANTHROPIC_API_KEY for Claude, or run Ollama ({OLLAMA_URL}) with a model pulled, e.g. ollama pull qwen2.5-coder:7b",
    }


async def _ollama_model() -> str:
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            r.raise_for_status()
            names = [m.get("name", "") for m in r.json().get("models", [])]
    except Exception:
        return ""
    if OLLAMA_MODEL:
        return OLLAMA_MODEL if OLLAMA_MODEL in names or f"{OLLAMA_MODEL}:latest" in names else ""
    coder = [n for n in names if "coder" in n.lower()]
    return (coder or names or [""])[0]


# ------------------------------------------------------------ Claude


_claude_client = None


def _claude():
    global _claude_client
    if _claude_client is None:
        import anthropic

        _claude_client = anthropic.AsyncAnthropic(max_retries=2)
    return _claude_client


async def _claude_stream(system: str, messages: list[dict], max_tokens: int, effort: str) -> AsyncIterator[str]:
    client = _claude()
    async with client.beta.messages.stream(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
        output_config={"effort": effort},
        cache_control={"type": "ephemeral"},
        betas=[_FALLBACK_BETA],
        fallbacks="default",
    ) as stream:
        async for text in stream.text_stream:
            yield text
        final = await stream.get_final_message()
    if final.stop_reason == "refusal":
        yield "\n\n_(The model declined to answer this request.)_"
    elif final.stop_reason == "max_tokens":
        yield "\n\n_(The answer was cut off: it got too long.)_"


async def _claude_text(system: str, messages: list[dict], max_tokens: int, effort: str) -> str:
    client = _claude()
    response = await client.beta.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
        output_config={"effort": effort},
        betas=[_FALLBACK_BETA],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        return ""
    return "".join(b.text for b in response.content if b.type == "text")


# ------------------------------------------------------------ Ollama


async def _ollama_stream(model: str, system: str, messages: list[dict], num_ctx: int = 8192) -> AsyncIterator[str]:
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, *messages],
        "stream": True,
        "keep_alive": "30m",
        "options": {"num_ctx": num_ctx, "temperature": 0.2},
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=5.0)) as client:
        async with client.stream("POST", f"{OLLAMA_URL}/api/chat", json=body) as r:
            if r.status_code != 200:
                raise AIUnavailable(f"Ollama answered {r.status_code}: {(await r.aread())[:200]!r}")
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if data.get("error"):
                    raise AIUnavailable(f"Ollama: {data['error']}")
                piece = (data.get("message") or {}).get("content") or ""
                if piece:
                    yield piece
                if data.get("done"):
                    break


async def _ollama_fim(model: str, prefix: str, suffix: str) -> str:
    body = {
        "model": model,
        "prompt": prefix,
        # Ollama's templates fill in the middle only when there is a suffix; without one (the
        # cursor at the end of the cell) they chat instead ("Sure! Here is...").
        "suffix": suffix or "\n",
        "stream": False,
        "raw": False,
        "keep_alive": "30m",
        "options": {"num_predict": 96, "temperature": 0.1, "num_ctx": 4096, "stop": ["\n\n\n", "<|endoftext|>", "<|fim_pad|>", "<|file_sep|>"]},
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=3.0)) as client:
        r = await client.post(f"{OLLAMA_URL}/api/generate", json=body)
        if r.status_code != 200:
            return ""
        return str(r.json().get("response") or "")


# ------------------------------------------------------------ the AI Hub


async def _hub_stream(role: str, system: str, messages: list[dict], max_tokens: int, effort: str, user: str) -> AsyncIterator[str]:
    reply = hub.stream(role, system, messages, max_tokens=max_tokens, effort=effort, user=user)
    try:
        async for piece in reply:
            yield piece
    except hub.HubUnavailable as e:
        raise AIUnavailable(str(e)) from e
    if reply.stop == "refusal":
        yield "\n\n_(The model declined to answer this request.)_"
    elif reply.stop == "max_tokens":
        yield "\n\n_(The answer was cut off: it got too long.)_"


# ------------------------------------------------------------ what the notebook asks


async def stream_chat(history: list[dict], question: str, context: str, user: str = "") -> AsyncIterator[str]:
    """The assistant's answer to ``question``, after the earlier turns in ``history``
    ([{"role", "content"}]), with the notebook as it is now; ``user`` is the person asking (for
    the AI Hub's log)."""
    info = await status()
    messages = [*history[-12:], {"role": "user", "content": f"{question}\n\n<notebook>\n{context}\n</notebook>"}]
    if info["provider"] == "hub":
        async for piece in _hub_stream(HUB_ROLES["chat"], SYSTEM_CHAT, messages, 16000, "medium", user):
            yield piece
    elif info["provider"] == "claude":
        async for piece in _claude_stream(SYSTEM_CHAT, messages, 16000, "medium"):
            yield piece
    elif info["provider"] == "ollama":
        async for piece in _ollama_stream(info["model"], SYSTEM_CHAT, messages):
            yield piece
    else:
        raise AIUnavailable(info["detail"])


async def stream_edit(instruction: str, cell_source: str, cell_type: str, context: str, error: str = "", user: str = "") -> AsyncIterator[str]:
    """The new content of a cell, as the model writes it (with its fence; see ``code_of``)."""
    info = await status()
    parts = [f"<notebook>\n{context}\n</notebook>", f"<target_cell type=\"{cell_type}\">\n{cell_source}\n</target_cell>"]
    if error:
        parts.append(f"<error>\n{error}\n</error>")
    parts.append(f"<instruction>\n{instruction}\n</instruction>")
    messages = [{"role": "user", "content": "\n\n".join(parts)}]
    if info["provider"] == "hub":
        async for piece in _hub_stream(HUB_ROLES["edit"], SYSTEM_EDIT, messages, 16000, "medium", user):
            yield piece
    elif info["provider"] == "claude":
        async for piece in _claude_stream(SYSTEM_EDIT, messages, 16000, "medium"):
            yield piece
    elif info["provider"] == "ollama":
        async for piece in _ollama_stream(info["model"], SYSTEM_EDIT, messages):
            yield piece
    else:
        raise AIUnavailable(info["detail"])


async def stream_notebook(request: str, user: str = "") -> AsyncIterator[str]:
    """A new notebook for ``request``, as Markdown with fenced code cells."""
    info = await status()
    messages = [{"role": "user", "content": request}]
    if info["provider"] == "hub":
        async for piece in _hub_stream(HUB_ROLES["notebook"], SYSTEM_NOTEBOOK, messages, 16000, "medium", user):
            yield piece
    elif info["provider"] == "claude":
        async for piece in _claude_stream(SYSTEM_NOTEBOOK, messages, 16000, "medium"):
            yield piece
    elif info["provider"] == "ollama":
        async for piece in _ollama_stream(info["model"], SYSTEM_NOTEBOOK, messages):
            yield piece
    else:
        raise AIUnavailable(info["detail"])


async def inline(prefix: str, suffix: str, earlier: str) -> str:
    """What to insert at the cursor ("" for nothing)."""
    info = await status()
    try:
        if info["provider"] == "ollama":
            head = (earlier + "\n\n") if earlier else ""
            text = await _ollama_fim(info["model"], head + prefix, suffix)
        elif info["provider"] in ("claude", "hub"):
            prompt = (f"<earlier_cells>\n{earlier}\n</earlier_cells>\n\n" if earlier else "") + f"<cell>\n{prefix}<CURSOR>{suffix}\n</cell>"
            if info["provider"] == "hub":
                text = await hub.complete(HUB_ROLES["inline"], SYSTEM_INLINE, [{"role": "user", "content": prompt}], max_tokens=400, effort="low")
            else:
                text = await _claude_text(SYSTEM_INLINE, [{"role": "user", "content": prompt}], 400, "low")
        else:
            return ""
    except Exception as e:
        log.info("inline suggestion failed: %s", e)
        return ""
    return clean_inline(text, prefix, suffix)


def clean_inline(text: str, prefix: str, suffix: str) -> str:
    """A suggestion trimmed to what helps: no fences, not what already follows the cursor, at
    most a handful of lines."""
    text = text.replace("<CURSOR>", "")
    if "```" in text and not text.lstrip().startswith("```"):
        # Prose around a code block: a chat answer, not a completion.
        return ""
    text = re.sub(r"^```[\w-]*\n?", "", text.strip("\n") if text.startswith("```") else text)
    text = re.sub(r"\n?```\s*$", "", text)
    if not text.strip() or _PROSE.match(text.lstrip()):
        return ""
    # The model sometimes repeats the line before the cursor: drop it.
    line_start = prefix[prefix.rfind("\n") + 1:]
    if line_start.strip() and text.startswith(line_start):
        text = text[len(line_start):]
    nxt = suffix.lstrip(" \t")
    if nxt:
        # Don't suggest what is already there right after the cursor.
        first = nxt.split("\n", 1)[0]
        if first and text.endswith(first):
            text = text[: -len(first)]
    lines = text.split("\n")
    if len(lines) > 8:
        text = "\n".join(lines[:8])
    return text.rstrip() if "\n" in text else text


_PROSE = re.compile(r"(Sure|Certainly|Here(?:'s| is| are)|Below|To |I |You can|This (?:code|function))\b")

_FENCED = re.compile(r"```[ \t]*([\w+-]*)[ \t]*\n(.*?)(?:\n```|$)", re.S)


def code_of(answer: str) -> str:
    """The cell content in an edit answer: the first fenced block (also while it is still being
    written, without its closing fence), else the answer itself."""
    m = _FENCED.search(answer)
    if m:
        return m.group(2).rstrip("\n").rstrip("`").rstrip("\n")
    stripped = answer.strip()
    if stripped.startswith("```"):
        return ""
    return stripped


def split_answer(answer: str) -> list[dict]:
    """A chat answer as parts: {"kind": "text"|"code", "text", "lang"}; code parts get Insert
    and Copy buttons."""
    parts: list[dict] = []
    pos = 0
    for m in re.finditer(r"```[ \t]*([\w+-]*)[ \t]*\n(.*?)(?:\n```[ \t]*(?:\n|$)|$)", answer, re.S):
        if m.start() > pos:
            text = answer[pos:m.start()].strip("\n")
            if text.strip():
                parts.append({"kind": "text", "text": text, "lang": ""})
        lang = (m.group(1) or "python").lower()
        parts.append({"kind": "code", "text": m.group(2).rstrip("\n"), "lang": lang})
        pos = m.end()
    rest = answer[pos:].strip("\n")
    if rest.strip():
        parts.append({"kind": "text", "text": rest, "lang": ""})
    return parts
