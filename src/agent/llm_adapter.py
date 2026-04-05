"""LLM Adapter — 统一 OpenAI / Anthropic 的 tool calling 接口差异。"""

from __future__ import annotations

import json
import os
from typing import Any

from dotenv import load_dotenv

from src.models import LLMResponse, TokenUsage, ToolCall
from src.tools.schemas import convert_to_anthropic_format

load_dotenv()


class LLMAdapter:
    """统一 OpenAI/Anthropic 的 tool calling 接口。"""

    def __init__(self, provider: str, model: str):
        """
        Args:
            provider: "openai" 或 "anthropic"
            model: 模型名称，如 "gpt-4o", "claude-sonnet-4-20250514"
        """
        self.provider = provider
        self.model = model
        self._client = None

    # ── lazy client init ──────────────────────────────────

    def _get_openai_client(self):
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=os.getenv("OPENAI_API_KEY"),
                base_url=os.getenv("OPENAI_BASE_URL"),
                timeout=float(os.getenv("PROTOCOL_TWIN_LLM_TIMEOUT_SEC", "120")),
            )
        return self._client

    def _get_anthropic_client(self):
        if self._client is None:
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(
                api_key=os.getenv("ANTHROPIC_API_KEY"),
                base_url=os.getenv("ANTHROPIC_BASE_URL"),
                timeout=float(os.getenv("PROTOCOL_TWIN_LLM_TIMEOUT_SEC", "120")),
            )
        return self._client

    # ── public API ────────────────────────────────────────

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
    ) -> LLMResponse:
        """统一的 LLM 调用接口。

        Args:
            messages: 消息列表（统一格式，含 system / user / assistant / tool）
            tools: Tool Schema 列表（OpenAI function calling 格式，内部自动转换）

        Returns:
            LLMResponse 统一响应结构
        """
        if self.provider == "openai":
            return await self._chat_openai(messages, tools)
        elif self.provider == "anthropic":
            return await self._chat_anthropic(messages, tools)
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

    def make_tool_result_message(self, tool_call_id: str, result: dict) -> dict:
        """构造 provider 特定的 tool result 消息。

        OpenAI:    {"role": "tool", "tool_call_id": ..., "content": ...}
        Anthropic: {"role": "user", "content": [{"type": "tool_result", ...}]}
        """
        content = json.dumps(result, ensure_ascii=False)
        if self.provider == "openai":
            return {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": content,
            }
        else:
            return {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_call_id,
                        "content": content,
                    }
                ],
            }

    # ── OpenAI implementation ─────────────────────────────

    async def _chat_openai(
        self, messages: list[dict], tools: list[dict]
    ) -> LLMResponse:
        if self._should_use_structured_output(messages, tools):
            return await self._chat_openai_structured(messages)

        return await self._chat_openai_once(messages, tools)

    async def _chat_openai_once(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        response_format: dict[str, Any] | None = None,
        tool_choice: dict[str, Any] | str | None = None,
    ) -> LLMResponse:
        client = self._get_openai_client()

        kwargs: dict = {
            "model": self.model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        if response_format is not None:
            kwargs["response_format"] = response_format
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice

        response = await client.chat.completions.create(**kwargs)
        self._validate_openai_response_shape(response)
        choice = response.choices[0]
        msg = choice.message

        # Parse tool calls
        tool_calls: list[ToolCall] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                args = tc.function.arguments
                if isinstance(args, str):
                    args = json.loads(args)
                tool_calls.append(
                    ToolCall(
                        name=tc.function.name,
                        arguments=args,
                        id=tc.id,
                    )
                )

        # Build raw_message for appending back to messages list
        raw: dict = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            raw["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": (
                            tc.function.arguments
                            if isinstance(tc.function.arguments, str)
                            else json.dumps(tc.function.arguments, ensure_ascii=False)
                        ),
                    },
                }
                for tc in msg.tool_calls
            ]

        # Token usage
        usage = TokenUsage()
        if response.usage:
            usage = TokenUsage(
                prompt_tokens=response.usage.prompt_tokens or 0,
                completion_tokens=response.usage.completion_tokens or 0,
            )

        return LLMResponse(
            has_tool_calls=len(tool_calls) > 0,
            tool_calls=tool_calls,
            text=msg.content or None,
            usage=usage,
            raw_message=raw,
        )

    async def _chat_openai_structured(
        self,
        messages: list[dict],
    ) -> LLMResponse:
        schema = self._structured_output_schema(messages)
        last_error: Exception | None = None

        if schema is not None:
            synthetic_tools = [self._structured_output_tool_schema(schema)]
            try:
                tool_response = await self._chat_openai_once(
                    messages,
                    synthetic_tools,
                    tool_choice={
                        "type": "function",
                        "function": {"name": self._structured_output_tool_name()},
                    },
                )
                normalized = self._normalize_structured_tool_response(tool_response)
                if normalized is not None:
                    return normalized
                if self._response_text_is_json_object(tool_response):
                    return tool_response
            except Exception as exc:  # noqa: BLE001
                last_error = exc

            try:
                schema_response = await self._chat_openai_once(
                    messages,
                    [],
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "structured_output",
                            "strict": True,
                            "schema": schema,
                        },
                    },
                )
                if self._response_text_is_json_object(schema_response):
                    return schema_response
            except Exception as exc:  # noqa: BLE001
                last_error = exc

        try:
            json_object_response = await self._chat_openai_once(
                messages,
                [],
                response_format={"type": "json_object"},
            )
            if self._response_text_is_json_object(json_object_response):
                return json_object_response
        except Exception as exc:  # noqa: BLE001
            last_error = exc

        try:
            return await self._chat_openai_once(messages, [])
        except Exception:
            if last_error is not None:
                raise last_error
            raise

    # ── Anthropic implementation ──────────────────────────

    async def _chat_anthropic(
        self, messages: list[dict], tools: list[dict]
    ) -> LLMResponse:
        client = self._get_anthropic_client()

        # Split system messages out — Anthropic uses a separate `system` param
        system_text, non_system_messages = self._split_system_messages(messages)

        # Convert tool schemas from OpenAI format to Anthropic format
        anthropic_tools = convert_to_anthropic_format(tools) if tools else []

        kwargs: dict = {
            "model": self.model,
            "messages": non_system_messages,
            "max_tokens": 4096,
        }
        if system_text:
            kwargs["system"] = system_text
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        response = await client.messages.create(**kwargs)

        # Parse tool calls from content blocks
        tool_calls: list[ToolCall] = []
        text_parts: list[str] = []

        for block in response.content:
            if block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        name=block.name,
                        arguments=block.input if isinstance(block.input, dict) else {},
                        id=block.id,
                    )
                )
            elif block.type == "text":
                text_parts.append(block.text)

        combined_text = "\n".join(text_parts) if text_parts else None

        # Build raw_message for Anthropic (serialize content blocks)
        raw: dict = {
            "role": "assistant",
            "content": [
                self._serialize_anthropic_block(b) for b in response.content
            ],
        }

        # Token usage
        usage = TokenUsage(
            prompt_tokens=response.usage.input_tokens or 0,
            completion_tokens=response.usage.output_tokens or 0,
        )

        return LLMResponse(
            has_tool_calls=len(tool_calls) > 0,
            tool_calls=tool_calls,
            text=combined_text,
            usage=usage,
            raw_message=raw,
        )

    # ── helpers ───────────────────────────────────────────

    @staticmethod
    def _split_system_messages(
        messages: list[dict],
    ) -> tuple[str, list[dict]]:
        """Extract system messages into a single string; return the rest."""
        system_parts: list[str] = []
        rest: list[dict] = []
        for msg in messages:
            if msg.get("role") == "system":
                system_parts.append(msg.get("content", ""))
            else:
                rest.append(msg)
        return "\n".join(system_parts), rest

    @staticmethod
    def _should_request_json_object(messages: list[dict], tools: list[dict]) -> bool:
        """Backward-compatible alias for tests and legacy callers."""
        return LLMAdapter._should_use_structured_output(messages, tools)

    @staticmethod
    def _should_use_structured_output(messages: list[dict], tools: list[dict]) -> bool:
        """Use structured-output enforcement for plain JSON-only prompts."""
        if tools:
            return False
        for msg in messages:
            if msg.get("role") != "system":
                continue
            content = str(msg.get("content", ""))
            if "Return JSON only" in content or "Return JSON only with this schema" in content:
                return True
        return False

    @staticmethod
    def _extract_first_json_object(text: str) -> dict[str, Any] | None:
        decoder = json.JSONDecoder()
        for index, char in enumerate(text):
            if char != "{":
                continue
            try:
                payload, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        return None

    @staticmethod
    def _infer_json_schema_from_example(value: Any) -> dict[str, Any]:
        if isinstance(value, bool):
            return {"type": "boolean"}
        if isinstance(value, int) and not isinstance(value, bool):
            return {"type": "integer"}
        if isinstance(value, float):
            return {"type": "number"}
        if isinstance(value, str):
            return {"type": "string"}
        if value is None:
            return {"type": "null"}
        if isinstance(value, list):
            item_schema = (
                LLMAdapter._infer_json_schema_from_example(value[0])
                if value else
                {}
            )
            return {"type": "array", "items": item_schema}
        if isinstance(value, dict):
            properties = {
                key: LLMAdapter._infer_json_schema_from_example(item)
                for key, item in value.items()
            }
            return {
                "type": "object",
                "properties": properties,
                "required": list(value.keys()),
                "additionalProperties": False,
            }
        return {}

    @classmethod
    def _structured_output_schema(cls, messages: list[dict]) -> dict[str, Any] | None:
        for msg in messages:
            if msg.get("role") != "system":
                continue
            content = str(msg.get("content", ""))
            example = cls._extract_first_json_object(content)
            if isinstance(example, dict):
                return cls._infer_json_schema_from_example(example)
        return {
            "type": "object",
            "additionalProperties": True,
        }

    @staticmethod
    def _structured_output_tool_name() -> str:
        return "emit_structured_response"

    @classmethod
    def _structured_output_tool_schema(cls, schema: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": cls._structured_output_tool_name(),
                "description": "Return the structured JSON response exactly once.",
                "parameters": schema,
            },
        }

    @classmethod
    def _normalize_structured_tool_response(cls, response: LLMResponse) -> LLMResponse | None:
        if not response.tool_calls:
            return None
        first_call = response.tool_calls[0]
        if first_call.name != cls._structured_output_tool_name():
            return None
        if not isinstance(first_call.arguments, dict):
            return None
        return LLMResponse(
            has_tool_calls=False,
            tool_calls=[],
            text=json.dumps(first_call.arguments, ensure_ascii=False),
            usage=response.usage,
            raw_message=response.raw_message,
        )

    @staticmethod
    def _response_text_is_json_object(response: LLMResponse) -> bool:
        text = (response.text or "").strip()
        if not text:
            return False
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return False
        return isinstance(payload, dict)

    @staticmethod
    def _validate_openai_response_shape(response) -> None:
        """Fail clearly when a gateway returns HTML/plain text instead of ChatCompletion."""
        choices = getattr(response, "choices", None)
        if isinstance(choices, list) and choices:
            return

        preview = response if isinstance(response, str) else repr(response)
        preview = preview.strip().replace("\n", " ")[:200]
        raise RuntimeError(
            "OpenAI-compatible endpoint returned a non-ChatCompletion payload. "
            f"Expected `.choices`, got {type(response).__name__}. "
            f"Preview: {preview}"
        )

    @staticmethod
    def _serialize_anthropic_block(block) -> dict:
        """Convert an Anthropic ContentBlock to a plain dict."""
        if block.type == "tool_use":
            return {
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            }
        elif block.type == "text":
            return {"type": "text", "text": block.text}
        # fallback
        return {"type": block.type}
