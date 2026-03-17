"""Tests for message assembly — unit tests + Hypothesis property tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import hypothesis.strategies as st
from hypothesis import given, settings

from src.agent import loop
from src.agent.loop import _append_context_reuse_message, PRECHECK_GUIDANCE
from src.models import LLMResponse, TokenUsage


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

    def finalize_turn(self, *args, **kwargs):
        return None

    def finalize_session(self):
        return None


class _FakeAdapter:
    def __init__(self, provider: str, model: str):
        self.captured_messages = None

    async def chat_with_tools(self, messages, tools):
        self.captured_messages = messages
        return LLMResponse(
            has_tool_calls=False,
            tool_calls=[],
            text="ok",
            usage=TokenUsage(prompt_tokens=1, completion_tokens=1),
            raw_message={"role": "assistant", "content": "ok"},
        )

    def make_tool_result_message(self, tool_call_id: str, result: dict) -> dict:
        return {}


# ── Unit Tests ──────────────────────────────────────────────


def test_context_summary_is_injected(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def make_cm():
        return _FakeContextManager(tmp_path / "sess_1")

    class CapturingAdapter(_FakeAdapter):
        async def chat_with_tools(self, messages, tools):
            captured["messages"] = messages
            return await super().chat_with_tools(messages, tools)

    monkeypatch.setattr(loop, "ContextManager", make_cm)
    monkeypatch.setattr(loop, "LLMAdapter", CapturingAdapter)
    monkeypatch.setattr(loop, "load_system_prompt", lambda *_: "system")
    monkeypatch.setattr(loop, "get_tool_schemas", lambda: [])
    monkeypatch.setattr(loop, "_save_session", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "src.agent.loop.ContextReuseBuilder.build_summary",
        lambda self, doc_name, query=None: "## 已读页面摘要\n- **Page 7**: cached",
    )

    test_loop = asyncio.new_event_loop()
    try:
        response = test_loop.run_until_complete(loop.agentic_rag(query="q", doc_name="doc.pdf"))
    finally:
        test_loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())

    assert response.context_session_id == "sess_1"
    messages = captured["messages"]
    assert messages[0]["role"] == "system"
    assert "Context Reuse Guidance" in messages[0]["content"]
    assert messages[1]["role"] == "system"
    assert messages[1]["content"].startswith("[Context from previous turns]")


def test_empty_context_keeps_original_message_shape(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def make_cm():
        return _FakeContextManager(tmp_path / "sess_1")

    class CapturingAdapter(_FakeAdapter):
        async def chat_with_tools(self, messages, tools):
            captured["messages"] = messages
            return await super().chat_with_tools(messages, tools)

    monkeypatch.setattr(loop, "ContextManager", make_cm)
    monkeypatch.setattr(loop, "LLMAdapter", CapturingAdapter)
    monkeypatch.setattr(loop, "load_system_prompt", lambda *_: "system")
    monkeypatch.setattr(loop, "get_tool_schemas", lambda: [])
    monkeypatch.setattr(loop, "_save_session", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.agent.loop.ContextReuseBuilder.build_summary", lambda self, doc_name, query=None: "")

    test_loop = asyncio.new_event_loop()
    try:
        test_loop.run_until_complete(loop.agentic_rag(query="q", doc_name="doc.pdf"))
    finally:
        test_loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())

    messages = captured["messages"]
    assert len(messages) == 2
    assert messages[0]["content"] == "system"


# ── Hypothesis Property Tests ───────────────────────────────


# Feature: context-reuse-enhancement, Property 4: 消息注入结构正确性
@given(
    context_text=st.text(min_size=10, max_size=200),
    n_history=st.integers(min_value=0, max_value=3),
)
@settings(max_examples=100)
def test_property_4_message_injection_structure(context_text: str, n_history: int) -> None:
    system_prompt = "You are a helpful assistant."
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"} for i in range(n_history)]
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": "question"})

    updated_messages, updated_prompt = _append_context_reuse_message(messages, system_prompt, context_text)

    # First message is system prompt with Precheck guidance appended
    assert updated_messages[0]["role"] == "system"
    assert "Context Reuse Guidance" in updated_messages[0]["content"]

    # Second message is context summary
    assert updated_messages[1]["role"] == "system"
    assert updated_messages[1]["content"].startswith("[Context from previous turns]")
    assert context_text in updated_messages[1]["content"]

    # Remaining messages are history + user
    assert len(updated_messages) == 2 + n_history + 1


# Feature: context-reuse-enhancement, Property 5: 空上下文保持原始行为
@given(
    n_history=st.integers(min_value=0, max_value=3),
)
@settings(max_examples=100)
def test_property_5_empty_context_preserves_original(n_history: int) -> None:
    system_prompt = "You are a helpful assistant."
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"} for i in range(n_history)]
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": "question"})

    updated_messages, updated_prompt = _append_context_reuse_message(messages, system_prompt, "")

    # No changes when context is empty
    assert updated_messages == messages
    assert updated_prompt == system_prompt
    assert "Context Reuse Guidance" not in updated_messages[0]["content"]
