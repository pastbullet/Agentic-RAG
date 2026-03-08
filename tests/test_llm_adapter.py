"""Tests for LLM Adapter — format conversion and message construction."""

from __future__ import annotations

import json

import pytest

from src.agent.llm_adapter import LLMAdapter


# ── make_tool_result_message tests ────────────────────────


class TestMakeToolResultMessage:
    """Verify provider-specific tool result message formats."""

    def test_openai_format(self):
        adapter = LLMAdapter(provider="openai", model="gpt-4o")
        result = {"content": [{"page": 7, "text": "hello"}]}
        msg = adapter.make_tool_result_message("call_abc123", result)

        assert msg["role"] == "tool"
        assert msg["tool_call_id"] == "call_abc123"
        assert json.loads(msg["content"]) == result

    def test_anthropic_format(self):
        adapter = LLMAdapter(provider="anthropic", model="claude-sonnet-4-20250514")
        result = {"content": [{"page": 7, "text": "hello"}]}
        msg = adapter.make_tool_result_message("toolu_abc123", result)

        assert msg["role"] == "user"
        assert isinstance(msg["content"], list)
        assert len(msg["content"]) == 1
        block = msg["content"][0]
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "toolu_abc123"
        assert json.loads(block["content"]) == result

    def test_openai_unicode_content(self):
        adapter = LLMAdapter(provider="openai", model="gpt-4o")
        result = {"text": "BFD 控制报文包含以下字段"}
        msg = adapter.make_tool_result_message("call_1", result)
        # ensure_ascii=False should preserve Chinese characters
        assert "BFD 控制报文包含以下字段" in msg["content"]

    def test_anthropic_unicode_content(self):
        adapter = LLMAdapter(provider="anthropic", model="claude-sonnet-4-20250514")
        result = {"text": "BFD 控制报文包含以下字段"}
        msg = adapter.make_tool_result_message("toolu_1", result)
        assert "BFD 控制报文包含以下字段" in msg["content"][0]["content"]


# ── _split_system_messages tests ──────────────────────────


class TestSplitSystemMessages:
    """Verify system message extraction for Anthropic adapter."""

    def test_single_system_message(self):
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
        ]
        system_text, rest = LLMAdapter._split_system_messages(messages)
        assert system_text == "You are a helpful assistant."
        assert len(rest) == 1
        assert rest[0]["role"] == "user"

    def test_multiple_system_messages(self):
        messages = [
            {"role": "system", "content": "Part 1"},
            {"role": "system", "content": "Part 2"},
            {"role": "user", "content": "Hello"},
        ]
        system_text, rest = LLMAdapter._split_system_messages(messages)
        assert "Part 1" in system_text
        assert "Part 2" in system_text
        assert len(rest) == 1

    def test_no_system_messages(self):
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        system_text, rest = LLMAdapter._split_system_messages(messages)
        assert system_text == ""
        assert len(rest) == 2

    def test_preserves_message_order(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
        ]
        _, rest = LLMAdapter._split_system_messages(messages)
        assert [m["role"] for m in rest] == ["user", "assistant", "user"]
        assert [m["content"] for m in rest] == ["q1", "a1", "q2"]


# ── constructor / provider validation ─────────────────────


class TestLLMAdapterInit:
    """Verify adapter initialization."""

    def test_openai_provider(self):
        adapter = LLMAdapter(provider="openai", model="gpt-4o")
        assert adapter.provider == "openai"
        assert adapter.model == "gpt-4o"

    def test_anthropic_provider(self):
        adapter = LLMAdapter(provider="anthropic", model="claude-sonnet-4-20250514")
        assert adapter.provider == "anthropic"
        assert adapter.model == "claude-sonnet-4-20250514"

    def test_unsupported_provider_raises(self):
        import asyncio

        adapter = LLMAdapter(provider="gemini", model="gemini-pro")
        with pytest.raises(ValueError, match="Unsupported provider"):
            asyncio.get_event_loop().run_until_complete(
                adapter.chat_with_tools(
                    [{"role": "user", "content": "hi"}], []
                )
            )
