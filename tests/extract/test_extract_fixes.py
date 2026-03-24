"""Tests for message extractor precision fixes."""

from __future__ import annotations

import json

import pytest

from src.extract.extractors.message import (
    AUTH_BOUNDARY_RULE,
    ECHO_PACKET_RULE,
    VARIABLE_LENGTH_RULE,
    MessageExtractor,
    _SYSTEM_PROMPT,
)
from src.models import LLMResponse


class FakeLLM:
    def __init__(self, text: str):
        self.provider = "openai"
        self.model = "mock-model"
        self._text = text

    async def chat_with_tools(self, messages, tools):
        return LLMResponse(text=self._text)


def test_message_prompt_contains_echo_variable_length_and_boundary_rules():
    assert ECHO_PACKET_RULE.strip() in _SYSTEM_PROMPT
    assert VARIABLE_LENGTH_RULE.strip() in _SYSTEM_PROMPT
    assert AUTH_BOUNDARY_RULE.strip() in _SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_message_extractor_clears_echo_packet_fake_fields():
    extractor = MessageExtractor(
        FakeLLM(
            json.dumps(
                {
                    "name": "BFD Echo Packet",
                    "fields": [
                        {
                            "name": "Auth Key/Hash",
                            "size_bits": 160,
                            "description": "incorrect hallucinated field",
                        }
                    ],
                }
            )
        )
    )

    result = await extractor.extract("echo-1", "echo packet text", "Echo Packet", source_pages=[14])

    assert result.name == "BFD Echo Packet"
    assert result.fields == []
    assert result.source_pages == [14]


@pytest.mark.asyncio
async def test_message_extractor_marks_password_field_as_variable_length():
    extractor = MessageExtractor(
        FakeLLM(
            json.dumps(
                {
                    "name": "BFD Simple Password Authentication Section",
                    "fields": [
                        {
                            "name": "Password",
                            "type": "bytes",
                            "size_bits": 8,
                            "description": "1-16 bytes, variable length",
                        }
                    ],
                }
            )
        )
    )

    result = await extractor.extract("msg-1", "password section text", "Simple Password", source_pages=[22])

    assert len(result.fields) == 1
    assert result.fields[0].name == "Password"
    assert result.fields[0].size_bits is None
