"""LLM Adapter — 统一 OpenAI / Anthropic 的 tool calling 接口差异。"""

from __future__ import annotations

import json
import os

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
        client = self._get_openai_client()

        kwargs: dict = {
            "model": self.model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        response = await client.chat.completions.create(**kwargs)
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
            text=msg.content if not tool_calls else None,
            usage=usage,
            raw_message=raw,
        )

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
            text=combined_text if not tool_calls else None,
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
