"""Tests for LLM Adapter — format conversion and message construction."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

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


class TestJsonObjectMode:
    def test_should_request_structured_output_for_json_only_prompt(self):
        messages = [
            {"role": "system", "content": "Return JSON only with this shape:\n{}"},
            {"role": "user", "content": "hello"},
        ]
        assert LLMAdapter._should_use_structured_output(messages, []) is True

    def test_should_not_request_structured_output_when_tools_present(self):
        messages = [
            {"role": "system", "content": "Return JSON only with this shape:\n{}"},
            {"role": "user", "content": "hello"},
        ]
        tools = [{"type": "function", "function": {"name": "noop", "parameters": {"type": "object"}}}]
        assert LLMAdapter._should_use_structured_output(messages, tools) is False

    def test_infers_schema_from_json_example(self):
        messages = [
            {
                "role": "system",
                "content": (
                    "Return JSON only with this shape:\n"
                    '{"label":"state_machine","candidate_labels":["..."],"confidence":0.0,"secondary_hints":["..."]}'
                ),
            },
            {"role": "user", "content": "hello"},
        ]

        schema = LLMAdapter._structured_output_schema(messages)

        assert schema["type"] == "object"
        assert schema["properties"]["label"]["type"] == "string"
        assert schema["properties"]["candidate_labels"]["type"] == "array"
        assert schema["properties"]["confidence"]["type"] == "number"

    def test_openai_structured_output_prefers_forced_tool(self):
        class FakeCreate:
            def __init__(self):
                self.calls: list[dict] = []

            async def __call__(self, **kwargs):
                self.calls.append(kwargs)
                tool_call = SimpleNamespace(
                    id="call_1",
                    function=SimpleNamespace(
                        name="emit_structured_response",
                        arguments='{"ok": true}',
                    ),
                )
                message = SimpleNamespace(content=None, tool_calls=[tool_call])
                choice = SimpleNamespace(message=message)
                usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)
                return SimpleNamespace(choices=[choice], usage=usage)

        fake_create = FakeCreate()
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=fake_create)
            )
        )
        adapter = LLMAdapter(provider="openai", model="gpt-4o")
        adapter._client = fake_client
        messages = [
            {"role": "system", "content": 'Return JSON only with this shape:\n{"ok": true}'},
            {"role": "user", "content": "hello"},
        ]

        response = asyncio.run(adapter.chat_with_tools(messages, []))

        assert response.text == '{"ok": true}'
        assert len(fake_create.calls) == 1
        assert "tools" in fake_create.calls[0]
        assert fake_create.calls[0]["tool_choice"] == {
            "type": "function",
            "function": {"name": "emit_structured_response"},
        }

    def test_openai_structured_output_falls_back_to_json_schema(self):
        class FakeCreate:
            def __init__(self):
                self.calls: list[dict] = []

            async def __call__(self, **kwargs):
                self.calls.append(kwargs)
                if "tools" in kwargs:
                    raise RuntimeError("tool forcing unsupported")
                message = SimpleNamespace(content='{"ok": true}', tool_calls=None)
                choice = SimpleNamespace(message=message)
                usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)
                return SimpleNamespace(choices=[choice], usage=usage)

        fake_create = FakeCreate()
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=fake_create)
            )
        )
        adapter = LLMAdapter(provider="openai", model="gpt-4o")
        adapter._client = fake_client
        messages = [
            {"role": "system", "content": 'Return JSON only with this shape:\n{"ok": true}'},
            {"role": "user", "content": "hello"},
        ]

        response = asyncio.run(adapter.chat_with_tools(messages, []))

        assert response.text == '{"ok": true}'
        assert len(fake_create.calls) == 2
        assert fake_create.calls[1]["response_format"]["type"] == "json_schema"

    def test_openai_structured_output_falls_back_to_json_object(self):
        class FakeCreate:
            def __init__(self):
                self.calls: list[dict] = []

            async def __call__(self, **kwargs):
                self.calls.append(kwargs)
                if "tools" in kwargs:
                    raise RuntimeError("tool forcing unsupported")
                if kwargs.get("response_format", {}).get("type") == "json_schema":
                    raise RuntimeError("json schema unsupported")
                message = SimpleNamespace(content='{"ok": true}', tool_calls=None)
                choice = SimpleNamespace(message=message)
                usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)
                return SimpleNamespace(choices=[choice], usage=usage)

        fake_create = FakeCreate()
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=fake_create)
            )
        )
        adapter = LLMAdapter(provider="openai", model="gpt-4o")
        adapter._client = fake_client
        messages = [
            {"role": "system", "content": 'Return JSON only with this shape:\n{"ok": true}'},
            {"role": "user", "content": "hello"},
        ]

        response = asyncio.run(adapter.chat_with_tools(messages, []))

        assert response.text == '{"ok": true}'
        assert len(fake_create.calls) == 3
        assert fake_create.calls[2]["response_format"] == {"type": "json_object"}

    def test_openai_response_shape_validation_rejects_plain_text(self):
        with pytest.raises(RuntimeError, match="non-ChatCompletion payload"):
            LLMAdapter._validate_openai_response_shape("<html>gateway</html>")
