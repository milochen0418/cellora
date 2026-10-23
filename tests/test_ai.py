"""The AI helpers: cleaning suggestions, reading answers, and the request sent to Claude."""

import asyncio
import json

import pytest

from cellora import ai


def test_clean_inline():
    assert ai.clean_inline("    return a + b", "def add(a, b):\n", "") == "    return a + b"
    # A chat answer is no completion.
    assert ai.clean_inline("Sure! Here is the function:\n```python\ndef f(): pass\n```", "def f", "") == ""
    assert ai.clean_inline("Here's how", "x", "") == ""
    # Fences around plain code are dropped.
    assert ai.clean_inline("```python\nprint(1)\n```", "", "") == "print(1)"
    # Not what already follows the cursor.
    assert ai.clean_inline("sum(values))", "total = ", ")") == "sum(values)"
    # At most a handful of lines.
    assert ai.clean_inline("\n".join(f"x{i} = {i}" for i in range(20)), "", "").count("\n") == 7


def test_code_of_and_split():
    assert ai.code_of("```python\nx = 1\ny = 2\n```") == "x = 1\ny = 2"
    assert ai.code_of("```python\nx = 1\ny =") == "x = 1\ny ="  # still being written
    assert ai.code_of("```") == ""
    parts = ai.split_answer("Use groupby:\n```python\ndf.groupby('a').sum()\n```\nThat's it.")
    assert [p["kind"] for p in parts] == ["text", "code", "text"]
    assert parts[1] == {"kind": "code", "text": "df.groupby('a').sum()", "lang": "python"}


def _sse(events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def test_claude_request(monkeypatch):
    """The chat request: the default model, effort, prompt caching and the server-side fallback
    for refusals; the streamed text comes through."""
    httpx2 = pytest.importorskip("httpx2")
    import anthropic

    seen = {}

    def handler(request):
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        events = [
            {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 0}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "West has "}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "the most."}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 5}},
            {"type": "message_stop"},
        ]
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=_sse(events))

    client = anthropic.AsyncAnthropic(api_key="test-key", http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)))
    monkeypatch.setattr(ai, "_claude_client", client)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ai, "_status_cache", (0.0, {}))

    async def run():
        out = ""
        async for piece in ai.stream_chat([], "Which region sells most?", "### Cell 1 (code)\nsales"):
            out += piece
        return out

    assert asyncio.run(run()) == "West has the most."
    body = seen["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["fallbacks"] == "default"
    assert body["output_config"] == {"effort": "medium"}
    assert body["cache_control"] == {"type": "ephemeral"}
    assert body["stream"] is True
    assert "temperature" not in body
    assert "server-side-fallback-2026-07-01" in seen["headers"].get("anthropic-beta", "")
    assert "<notebook>" in body["messages"][-1]["content"]


def test_refusal_is_said(monkeypatch):
    httpx2 = pytest.importorskip("httpx2")
    import anthropic

    def handler(request):
        events = [
            {"type": "message_start", "message": {"id": "msg_2", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 0}}},
            {"type": "message_delta", "delta": {"stop_reason": "refusal", "stop_sequence": None}, "usage": {"output_tokens": 0}},
            {"type": "message_stop"},
        ]
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=_sse(events))

    client = anthropic.AsyncAnthropic(api_key="k", http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)))
    monkeypatch.setattr(ai, "_claude_client", client)

    async def run():
        return "".join([p async for p in ai._claude_stream("s", [{"role": "user", "content": "q"}], 100, "low")])

    assert "declined" in asyncio.run(run())
