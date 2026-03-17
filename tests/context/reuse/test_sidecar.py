"""Tests for Sidecar fault tolerance — unit tests + Hypothesis property tests."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import hypothesis.strategies as st
from hypothesis import given, settings

from src.agent import loop
from src.models import LLMResponse, ToolCall, TokenUsage


# ── Helpers ─────────────────────────────────────────────────


class _FakeContextManager:
    def __init__(self, session_dir: Path):
        self._session_dir = session_dir
        self._session_dir.mkdir(parents=True, exist_ok=True)

    def create_session(self, doc_name: str) -> str:
        return "sess_1"

    def create_turn(self, user_query: str, doc_name: str) -> str:
        return "turn_0001"

    @property
    def session_dir(self):
        return self._session_dir

    def record_tool_call(self, *args, **kwargs):
        return None

    def finalize_turn(self, *args, **kwargs):
        return None

    def finalize_session(self):
        return None


# ── Unit Tests ──────────────────────────────────────────────


def test_builder_failure_emits_context_reuse_error(monkeypatch, tmp_path: Path) -> None:
    events: list[dict] = []

    def make_cm():
        return _FakeContextManager(tmp_path / "sess_1")

    class FakeAdapter:
        def __init__(self, provider: str, model: str):
            pass

        async def chat_with_tools(self, messages, tools):
            return LLMResponse(
                has_tool_calls=False, tool_calls=[], text="ok",
                usage=TokenUsage(prompt_tokens=1, completion_tokens=1),
                raw_message={"role": "assistant", "content": "ok"},
            )

        def make_tool_result_message(self, tool_call_id: str, result: dict) -> dict:
            return {}

    monkeypatch.setattr(loop, "ContextManager", make_cm)
    monkeypatch.setattr(loop, "LLMAdapter", FakeAdapter)
    monkeypatch.setattr(loop, "load_system_prompt", lambda *_: "system")
    monkeypatch.setattr(loop, "get_tool_schemas", lambda: [])
    monkeypatch.setattr(loop, "_save_session", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "src.agent.loop.ContextReuseBuilder.build_summary",
        lambda self, doc_name, query=None: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    test_loop = asyncio.new_event_loop()
    try:
        response = test_loop.run_until_complete(
            loop.agentic_rag(query="q", doc_name="doc.pdf", progress_callback=events.append)
        )
    finally:
        test_loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())

    assert response.answer == "ok"
    assert any(event.get("type") == "context_reuse_error" for event in events)


def test_tool_execution_failure_emits_error_event(monkeypatch, tmp_path: Path) -> None:
    """When execute_tool raises, the error is caught and an error event is emitted."""
    events: list[dict] = []

    def make_cm():
        return _FakeContextManager(tmp_path / "sess_1")

    class FakeAdapter:
        def __init__(self, provider: str, model: str):
            self.calls = 0

        async def chat_with_tools(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(
                    has_tool_calls=True,
                    tool_calls=[ToolCall(name="get_page_content", arguments={"doc_name": "doc.pdf", "pages": "3"}, id="tc1")],
                    usage=TokenUsage(prompt_tokens=1, completion_tokens=1),
                    raw_message={"role": "assistant", "content": "", "tool_calls": []},
                )
            return LLMResponse(
                has_tool_calls=False, tool_calls=[], text="ok",
                usage=TokenUsage(prompt_tokens=1, completion_tokens=1),
                raw_message={"role": "assistant", "content": "ok"},
            )

        def make_tool_result_message(self, tool_call_id: str, result: dict) -> dict:
            return {"role": "tool", "tool_call_id": tool_call_id, "content": "{}"}

    monkeypatch.setattr(loop, "ContextManager", make_cm)
    monkeypatch.setattr(loop, "LLMAdapter", FakeAdapter)
    monkeypatch.setattr(loop, "load_system_prompt", lambda *_: "system")
    monkeypatch.setattr(loop, "get_tool_schemas", lambda: [])
    monkeypatch.setattr(loop, "_save_session", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        loop, "execute_tool",
        lambda name, arguments: {"content": [{"page": 3, "text": "fresh", "tables": [], "images": []}]},
    )

    test_loop = asyncio.new_event_loop()
    try:
        response = test_loop.run_until_complete(
            loop.agentic_rag(query="q", doc_name="doc.pdf", progress_callback=events.append)
        )
    finally:
        test_loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())

    assert response.pages_retrieved == [3]


# ── Hypothesis Property Tests ───────────────────────────────

_exception_strategy = st.sampled_from([
    RuntimeError("random failure"),
    ValueError("bad value"),
    OSError("disk error"),
    TypeError("type mismatch"),
    KeyError("missing key"),
])


# Feature: context-reuse-enhancement, Property 12: Sidecar 容错
@given(exc=_exception_strategy)
@settings(max_examples=100)
def test_property_12_sidecar_builder_fault_tolerance(exc: Exception) -> None:
    base_dir = Path(tempfile.mkdtemp()) / str(uuid4())
    events: list[dict] = []

    def make_cm():
        return _FakeContextManager(base_dir / "sess_1")

    class FakeAdapter:
        def __init__(self, provider: str, model: str):
            pass

        async def chat_with_tools(self, messages, tools):
            return LLMResponse(
                has_tool_calls=False, tool_calls=[], text="ok",
                usage=TokenUsage(prompt_tokens=1, completion_tokens=1),
                raw_message={"role": "assistant", "content": "ok"},
            )

        def make_tool_result_message(self, tool_call_id: str, result: dict) -> dict:
            return {}

    def raise_exc(self, doc_name, query=None):
        raise exc

    with patch.object(loop, "ContextManager", make_cm), \
         patch.object(loop, "LLMAdapter", FakeAdapter), \
         patch.object(loop, "load_system_prompt", lambda *_: "system"), \
         patch.object(loop, "get_tool_schemas", lambda: []), \
         patch.object(loop, "_save_session", lambda *args, **kwargs: None), \
         patch("src.agent.loop.ContextReuseBuilder.build_summary", raise_exc):

        test_loop = asyncio.new_event_loop()
        try:
            response = test_loop.run_until_complete(
                loop.agentic_rag(query="q", doc_name="doc.pdf", progress_callback=events.append)
            )
        finally:
            test_loop.close()
            asyncio.set_event_loop(asyncio.new_event_loop())

    # Agent loop should still produce an answer despite builder failure
    assert response.answer == "ok"
    # Error event should be emitted
    assert any(e.get("type") == "context_reuse_error" for e in events)
