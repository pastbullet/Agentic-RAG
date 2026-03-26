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


@pytest.mark.asyncio
async def test_message_extractor_attaches_tcp_archetype_sidecar():
    extractor = MessageExtractor(
        FakeLLM(
            json.dumps(
                {
                    "name": "TCP Header",
                    "fields": [
                        {"name": "Source Port", "size_bits": 16, "description": "The source port number."},
                        {"name": "Destination Port", "size_bits": 16, "description": "The destination port number."},
                        {"name": "Sequence Number", "size_bits": 32, "description": "Sequence number."},
                        {"name": "Acknowledgment Number", "size_bits": 32, "description": "Acknowledgment number."},
                        {"name": "Data Offset", "size_bits": 4, "description": "Header size in 32-bit words."},
                        {"name": "Reserved", "size_bits": 6, "description": "Reserved. Must be zero."},
                        {"name": "URG", "size_bits": 1, "description": "Urgent."},
                        {"name": "ACK", "size_bits": 1, "description": "Ack."},
                        {"name": "PSH", "size_bits": 1, "description": "Push."},
                        {"name": "RST", "size_bits": 1, "description": "Reset."},
                        {"name": "SYN", "size_bits": 1, "description": "Sync."},
                        {"name": "FIN", "size_bits": 1, "description": "Finish."},
                        {"name": "Window", "size_bits": 16, "description": "Window."},
                        {"name": "Checksum", "size_bits": 16, "description": "Checksum."},
                        {"name": "Urgent Pointer", "size_bits": 16, "description": "Urgent pointer."},
                        {"name": "Options", "size_bits": None, "description": "Variable-length options tail."},
                        {"name": "Padding", "size_bits": None, "description": "Derived zero padding."},
                    ],
                }
            )
        )
    )

    result = await extractor.extract("tcp-1", "tcp header text", "TCP Header", source_pages=[21, 22, 23, 24])

    assert result.archetype_contribution is not None
    assert result.archetype_contribution["canonical_hint"] == "tcp_header"
    assert result.archetype_contribution["tail_slots"][0]["slot_name"] == "options_tail"
