"""Tests for protocol extraction extractors."""

from __future__ import annotations

import json

import pytest

from src.models import LLMResponse
from src.extract.extractors import (
    ErrorExtractor,
    MessageExtractor,
    ProcedureExtractor,
    StateMachineExtractor,
    TimerExtractor,
)


class FakeLLM:
    def __init__(self, text: str):
        self.provider = "openai"
        self.model = "mock-model"
        self._text = text

    async def chat_with_tools(self, messages, tools):
        return LLMResponse(text=self._text)


@pytest.mark.asyncio
async def test_state_machine_extractor_parses_valid_json():
    llm = FakeLLM(
        json.dumps(
            {
                "name": "BFD Session",
                "states": [
                    {"name": "Down", "description": "Session down", "is_initial": True},
                    {"name": "Up", "description": "Session up", "is_final": False},
                ],
                "transitions": [
                    {
                        "from_state": "Down",
                        "to_state": "Up",
                        "event": "Receive packet",
                        "condition": "Valid discriminators",
                        "actions": ["Start timers"],
                    }
                ],
            }
        )
    )

    result = await StateMachineExtractor(llm).extract(
        node_id="n1",
        text="state machine text",
        title="State Machine",
        source_pages=[12, 13],
    )

    assert result.name == "BFD Session"
    assert len(result.states) == 2
    assert len(result.transitions) == 1
    assert result.source_pages == [12, 13]


@pytest.mark.asyncio
async def test_state_machine_extractor_returns_empty_model_on_invalid_json():
    llm = FakeLLM("not-json")
    result = await StateMachineExtractor(llm).extract(
        node_id="n2",
        text="invalid response test",
        title="Broken State Machine",
        source_pages=[7],
    )
    assert result.name == "Broken State Machine"
    assert result.states == []
    assert result.transitions == []
    assert result.source_pages == [7]


@pytest.mark.asyncio
async def test_state_machine_extractor_backfills_name_for_explicit_empty_fsm():
    llm = FakeLLM(json.dumps({"name": "", "states": [], "transitions": []}))
    result = await StateMachineExtractor(llm).extract(
        node_id="n-empty",
        text="This is a numbered check, not a standalone FSM.",
        title="Third Check for SYN",
        source_pages=[8],
    )

    assert result.name == "Third Check for SYN"
    assert result.states == []
    assert result.transitions == []
    assert result.source_pages == [8]


@pytest.mark.asyncio
async def test_state_machine_extractor_coerces_null_condition_to_empty_string():
    llm = FakeLLM(
        json.dumps(
            {
                "name": "TCP STATUS Call",
                "states": [
                    {"name": "ESTABLISHED", "description": "connected", "is_initial": False},
                    {"name": "CLOSED", "description": "closed", "is_final": True},
                ],
                "transitions": [
                    {
                        "from_state": "ESTABLISHED",
                        "to_state": "ESTABLISHED",
                        "event": "STATUS call",
                        "condition": None,
                        "actions": ["Return connection status"],
                    }
                ],
            }
        )
    )

    result = await StateMachineExtractor(llm).extract(
        node_id="n3",
        text="status call state machine text",
        title="STATUS Call",
        source_pages=[69, 70],
    )

    assert result.name == "TCP STATUS Call"
    assert len(result.states) == 2
    assert len(result.transitions) == 1
    assert result.transitions[0].condition == ""
    assert result.source_pages == [69, 70]


@pytest.mark.asyncio
async def test_state_machine_extractor_normalizes_alias_fields_for_states_and_transitions():
    llm = FakeLLM(
        json.dumps(
            {
                "name": "BFD State Machine",
                "states": [
                    {"id": "Down", "description": "Session down", "initial": True},
                    {"label": "Init", "details": "Session initializing"},
                    {"title": "Up", "summary": "Session established"},
                ],
                "transitions": [
                    {
                        "from": "Down",
                        "to": "Init",
                        "trigger": "receive remote Down",
                        "action": "start transmit timer",
                    },
                    {
                        "source": "Init",
                        "target": "Up",
                        "when": "receive remote Init",
                        "effects": ["set session up"],
                    },
                ],
            }
        )
    )

    result = await StateMachineExtractor(llm).extract(
        node_id="n-alias",
        text="Alias-shaped state machine payload",
        title="6.2 BFD State Machine",
        source_pages=[10, 11],
    )

    assert result.name == "BFD State Machine"
    assert [state.name for state in result.states] == ["Down", "Init", "Up"]
    assert result.states[0].is_initial is True
    assert result.transitions[0].from_state == "Down"
    assert result.transitions[0].to_state == "Init"
    assert result.transitions[0].event == "receive remote Down"
    assert result.transitions[0].actions == ["start transmit timer"]
    assert result.transitions[1].from_state == "Init"
    assert result.transitions[1].to_state == "Up"
    assert result.transitions[1].event == "receive remote Init"
    assert result.transitions[1].actions == ["set session up"]
    assert result.source_pages == [10, 11]


@pytest.mark.asyncio
async def test_state_machine_extractor_coerces_non_standalone_payload_to_empty():
    llm = FakeLLM(
        json.dumps(
            {
                "name": "Copied node metadata",
                "states": [{"id": "0019", "title": "6.3 Demultiplexing and the Discriminator Fields"}],
                "transitions": [],
            }
        )
    )

    result = await StateMachineExtractor(llm).extract(
        node_id="0019",
        text="This section discusses discriminators and packet demultiplexing.",
        title="6.3 Demultiplexing and the Discriminator Fields",
        source_pages=[16],
    )

    assert result.name == "6.3 Demultiplexing and the Discriminator Fields"
    assert result.states == []
    assert result.transitions == []
    assert result.source_pages == [16]


@pytest.mark.asyncio
async def test_message_extractor_parses_fields_and_source_pages():
    llm = FakeLLM(
        json.dumps(
            {
                "name": "BFD Control Packet",
                "fields": [
                    {"name": "Version", "type": "uint", "size_bits": 3, "description": "Protocol version"},
                    {"name": "State", "type": "enum", "size_bits": 2, "description": "Session state"},
                ],
            }
        )
    )
    result = await MessageExtractor(llm).extract(
        node_id="m1",
        text="field table text",
        title="Packet Format",
        source_pages=[5],
    )
    assert result.name == "BFD Control Packet"
    assert [field.name for field in result.fields] == ["Version", "State"]
    assert result.source_pages == [5]


@pytest.mark.asyncio
async def test_procedure_timer_and_error_extractors_parse_payloads():
    procedure = await ProcedureExtractor(
        FakeLLM(
            json.dumps(
                {
                    "name": "Session Bringup",
                    "steps": [
                        {"step_number": 1, "condition": "Packet valid", "action": "Advance state"}
                    ],
                }
            )
        )
    ).extract("p1", "procedure text", "Procedure", source_pages=[3])
    timer = await TimerExtractor(
        FakeLLM(
            json.dumps(
                {
                    "timer_name": "Detection Time",
                    "timeout_value": "3 * MinRx",
                    "trigger_action": "Declare session down",
                    "description": "Liveness timeout",
                }
            )
        )
    ).extract("t1", "timer text", "Timer", source_pages=[9])
    error = await ErrorExtractor(
        FakeLLM(
            json.dumps(
                {
                    "error_condition": "Bad auth",
                    "handling_action": "Discard packet",
                    "description": "Authentication failed",
                }
            )
        )
    ).extract("e1", "error text", "Error", source_pages=[10])

    assert procedure.steps[0].action == "Advance state"
    assert procedure.source_pages == [3]
    assert timer.timer_name == "Detection Time"
    assert timer.source_pages == [9]
    assert error.handling_action == "Discard packet"
    assert error.source_pages == [10]


@pytest.mark.asyncio
async def test_extractors_return_empty_models_for_empty_text():
    sm = await StateMachineExtractor(FakeLLM("{}")).extract("s1", "", "SM", source_pages=[1])
    msg = await MessageExtractor(FakeLLM("{}")).extract("m1", "", "MSG", source_pages=[2])
    proc = await ProcedureExtractor(FakeLLM("{}")).extract("p1", "", "PROC", source_pages=[3])
    timer = await TimerExtractor(FakeLLM("{}")).extract("t1", "", "TIMER", source_pages=[4])
    err = await ErrorExtractor(FakeLLM("{}")).extract("e1", "", "ERR", source_pages=[5])

    assert sm.name == "SM" and sm.source_pages == [1]
    assert msg.name == "MSG" and msg.source_pages == [2]
    assert proc.name == "PROC" and proc.source_pages == [3]
    assert timer.timer_name == "TIMER" and timer.source_pages == [4]
    assert err.error_condition == "ERR" and err.source_pages == [5]
