"""Base classes and shared helpers for extractors."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from src.agent.llm_adapter import LLMAdapter


class BaseExtractor(ABC):
    """Base class for node extractors."""

    def __init__(self, llm: LLMAdapter):
        self.llm = llm

    async def _invoke_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        response = await self.llm.chat_with_tools(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            [],
        )
        text = (response.text or "").strip()
        if not text:
            raise ValueError("LLM returned empty response")

        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass

        if "```" in text:
            for block in text.split("```"):
                block = block.strip()
                if block.startswith("json"):
                    block = block[4:].strip()
                if not block:
                    continue
                try:
                    payload = json.loads(block)
                    if isinstance(payload, dict):
                        return payload
                except json.JSONDecodeError:
                    continue

        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            payload = json.loads(text[start : end + 1])
            if isinstance(payload, dict):
                return payload

        raise ValueError("Failed to parse JSON response")

    @abstractmethod
    async def extract(
        self,
        node_id: str,
        text: str,
        title: str,
        source_pages: list[int] | None = None,
    ):
        """Extract structured data from a node."""
