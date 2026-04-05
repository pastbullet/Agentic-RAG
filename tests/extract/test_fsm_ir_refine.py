"""Tests for Phase C FSM IR refine helpers and async refinement."""

from __future__ import annotations

import json

import pytest

from src.extract.fsm_ir import (
    ProtocolHint,
    _accept_llm_action,
    _accept_llm_guard,
    _llm_refine_branch,
    _needs_refinement,
    build_protocol_hint,
    lower_state_machine_to_fsm_ir,
    refine_fsm_irs,
)
from src.models import (
    LLMResponse,
    NormalizationStatus,
    ProcedureRule,
    ProcedureStep,
    ProtocolField,
    ProtocolMessage,
    ProtocolSchema,
    ProtocolState,
    ProtocolStateMachine,
    ProtocolTransition,
    TimerConfig,
    TypedAction,
    TypedGuard,
)


class StaticRefineLLM:
    def __init__(self, payload):
        self.payload = payload

    async def chat_with_tools(self, messages, tools):
        assert tools == []
        if isinstance(self.payload, str):
            return LLMResponse(text=self.payload)
        return LLMResponse(text=json.dumps(self.payload))


class SequenceRefineLLM:
    def __init__(self, payloads):
        self.payloads = list(payloads)

    async def chat_with_tools(self, messages, tools):
        assert tools == []
        payload = self.payloads.pop(0)
        return LLMResponse(text=json.dumps(payload))


def _make_schema_with_tcp_hints() -> tuple[ProtocolSchema, list]:
    state_machine = ProtocolStateMachine(
        name="TCP Main",
        states=[
            ProtocolState(name="LISTEN", is_initial=True),
            ProtocolState(name="ESTABLISHED"),
        ],
        transitions=[
            ProtocolTransition(
                from_state="LISTEN",
                to_state="ESTABLISHED",
                event="SEGMENT_ARRIVAL",
                condition="SEG.ACK is acceptable",
                actions=["advance SND.UNA", "cancel retransmit timer"],
            )
        ],
        source_pages=[1],
    )
    schema = ProtocolSchema(
        protocol_name="rfc793-TCP",
        state_machines=[state_machine],
        messages=[
            ProtocolMessage(
                name="TCP Header",
                fields=[
                    ProtocolField(name="Ack Number", size_bits=32),
                    ProtocolField(name="Sequence Number", size_bits=32),
                ],
            )
        ],
        procedures=[
            ProcedureRule(
                name="SEG processing",
                steps=[
                    ProcedureStep(
                        step_number=1,
                        condition="SEG.SEQ is acceptable",
                        action="set RCV.NXT to SEG.SEQ",
                    )
                ],
            )
        ],
        timers=[TimerConfig(timer_name="Retransmit Timer")],
    )
    fsm_irs = [lower_state_machine_to_fsm_ir(state_machine, schema.protocol_name)]
    return schema, fsm_irs


def _base_hint() -> ProtocolHint:
    return ProtocolHint(
        known_states=["LISTEN", "ESTABLISHED"],
        known_timers=["Retransmit Timer"],
        known_message_names=["TCP Header"],
        known_message_field_names=["Ack Number", "Sequence Number"],
        observed_context_tokens=["segment_ack", "send_unacked", "recv_next_seq", "auth_flag"],
    )


def test_build_protocol_hint_handles_empty_schema():
    hint = build_protocol_hint(ProtocolSchema(protocol_name="proto"), [])

    assert hint.known_states == []
    assert hint.known_timers == []
    assert hint.known_message_names == []
    assert hint.known_message_field_names == []
    assert hint.observed_context_tokens == []


def test_build_protocol_hint_collects_dotted_tokens_and_existing_ctx_refs():
    schema, fsm_irs = _make_schema_with_tcp_hints()
    hint = build_protocol_hint(schema, fsm_irs)

    assert hint.known_states == ["ESTABLISHED", "LISTEN"]
    assert hint.known_timers == ["Retransmit Timer"]
    assert hint.known_message_names == ["TCP Header"]
    assert "Ack Number" in hint.known_message_field_names
    assert "segment_ack" in hint.observed_context_tokens
    assert "send_unacked" in hint.observed_context_tokens
    assert "recv_next_seq" in hint.observed_context_tokens


def test_needs_refinement_stays_below_combined_threshold():
    state_machine = ProtocolStateMachine(
        name="Small FSM",
        states=[ProtocolState(name="A", is_initial=True), ProtocolState(name="B")],
        transitions=[
            ProtocolTransition(from_state="A", to_state="B", event="E1"),
            ProtocolTransition(from_state="B", to_state="A", event="E2"),
            ProtocolTransition(from_state="A", to_state="B", event="E3", condition="complex raw guard"),
        ],
    )
    ir = lower_state_machine_to_fsm_ir(state_machine, "proto")

    assert _needs_refinement(ir) is False


def test_needs_refinement_triggers_when_ratio_and_count_are_both_high():
    state_machine = ProtocolStateMachine(
        name="Large FSM",
        states=[ProtocolState(name="A", is_initial=True), ProtocolState(name="B"), ProtocolState(name="C")],
        transitions=[
            ProtocolTransition(from_state="A", to_state="B", event="E1", condition="complex guard one"),
            ProtocolTransition(from_state="A", to_state="C", event="E1", actions=["raw action one"]),
            ProtocolTransition(from_state="B", to_state="C", event="E2"),
        ],
    )
    ir = lower_state_machine_to_fsm_ir(state_machine, "proto")

    assert _needs_refinement(ir) is True


def test_acceptance_gate_rejects_unsupported_candidates():
    hint = _base_hint()

    assert _accept_llm_action(
        {"kind": "set_state", "target": "CLOSED"},
        raw_action_text="set closed",
        hint=hint,
        protocol_name="rfc793-TCP",
    ) is None
    assert _accept_llm_action(
        {"kind": "start_timer", "target": "Keepalive Timer"},
        raw_action_text="start keepalive",
        hint=hint,
        protocol_name="rfc793-TCP",
    ) is None
    assert _accept_llm_action(
        {"kind": "update_field", "ref_source": "msg", "target": "SEG.ACK", "value": "0"},
        raw_action_text="copy ack",
        hint=hint,
        protocol_name="rfc793-TCP",
    ) is None
    assert _accept_llm_action(
        {"kind": "update_field", "ref_source": "ctx", "target": "SND.UNA", "value": "SEG.ACK + 1"},
        raw_action_text="advance snd.una",
        hint=hint,
        protocol_name="rfc793-TCP",
    ) is None
    assert _accept_llm_action(
        {"kind": "emit_message", "target": "ACK"},
        raw_action_text="send ack",
        hint=hint,
        protocol_name="rfc793-TCP",
    ) is None
    assert _accept_llm_guard(
        {"kind": "timer_fired", "field_ref": "Retransmit Timer"},
        raw_guard_text="timer expired",
        hint=hint,
        protocol_name="rfc793-TCP",
    ) is None
    assert _accept_llm_guard(
        {"kind": "context_field_eq", "ref_source": "ctx", "field_ref": "SEG.ACK", "operator": ">", "value": "0"},
        raw_guard_text="seg ack acceptable",
        hint=hint,
        protocol_name="rfc793-TCP",
    ) is None
    assert _accept_llm_guard(
        {"kind": "always"},
        raw_guard_text="Otherwise",
        hint=hint,
        protocol_name="rfc793-TCP",
    ) is None


def test_acceptance_gate_accepts_codegen_first_subset():
    hint = _base_hint()

    assert _accept_llm_action(
        {"kind": "set_state", "target": "ESTABLISHED"},
        raw_action_text="enter established",
        hint=hint,
        protocol_name="rfc793-TCP",
    ) == TypedAction(kind="set_state", ref_source="ctx", target="ESTABLISHED", description="enter established")
    assert _accept_llm_action(
        {"kind": "start_timer", "target": "Retransmit Timer"},
        raw_action_text="arm timer",
        hint=hint,
        protocol_name="rfc793-TCP",
    ) == TypedAction(kind="start_timer", ref_source="timer", target="Retransmit Timer", description="arm timer")
    assert _accept_llm_action(
        {"kind": "cancel_timer", "target": "Retransmit Timer"},
        raw_action_text="stop timer",
        hint=hint,
        protocol_name="rfc793-TCP",
    ) == TypedAction(kind="cancel_timer", ref_source="timer", target="Retransmit Timer", description="stop timer")
    assert _accept_llm_action(
        {"kind": "update_field", "ref_source": "ctx", "target": "SND.UNA", "value": "0"},
        raw_action_text="advance snd.una",
        hint=hint,
        protocol_name="rfc793-TCP",
    ) == TypedAction(
        kind="update_field",
        ref_source="ctx",
        target="SND.UNA",
        value="0",
        description="advance snd.una",
    )
    assert _accept_llm_guard(
        {"kind": "context_field_eq", "ref_source": "ctx", "field_ref": "SEG.ACK", "operator": "==", "value": "0"},
        raw_guard_text="seg ack acceptable",
        hint=hint,
        protocol_name="rfc793-TCP",
    ) == TypedGuard(
        kind="context_field_eq",
        ref_source="ctx",
        field_ref="SEG.ACK",
        operator="==",
        value="0",
        description="seg ack acceptable",
    )
    assert _accept_llm_guard(
        {"kind": "context_field_ne", "ref_source": "ctx", "field_ref": "SEG.ACK", "operator": "!=", "value": "ESTABLISHED"},
        raw_guard_text="seg ack differs",
        hint=hint,
        protocol_name="rfc793-TCP",
    ) == TypedGuard(
        kind="context_field_ne",
        ref_source="ctx",
        field_ref="SEG.ACK",
        operator="!=",
        value="ESTABLISHED",
        description="seg ack differs",
    )


@pytest.mark.asyncio
async def test_llm_refine_branch_keeps_raw_when_llm_returns_unresolved():
    schema, fsm_irs = _make_schema_with_tcp_hints()
    hint = build_protocol_hint(schema, fsm_irs)
    block = fsm_irs[0].blocks[0]
    branch = block.branches[0]

    refined_branch, accepted_guard_count, accepted_action_count = await _llm_refine_branch(
        fsm_irs[0].name,
        schema.protocol_name,
        block,
        branch,
        StaticRefineLLM({"guard": None, "actions": [None]}),
        hint,
    )

    assert refined_branch == branch
    assert accepted_guard_count == 0
    assert accepted_action_count == 0


@pytest.mark.asyncio
async def test_llm_refine_branch_ignores_invalid_json():
    schema, fsm_irs = _make_schema_with_tcp_hints()
    hint = build_protocol_hint(schema, fsm_irs)
    block = fsm_irs[0].blocks[0]
    branch = block.branches[0]

    refined_branch, accepted_guard_count, accepted_action_count = await _llm_refine_branch(
        fsm_irs[0].name,
        schema.protocol_name,
        block,
        branch,
        StaticRefineLLM("not-json"),
        hint,
    )

    assert refined_branch == branch
    assert accepted_guard_count == 0
    assert accepted_action_count == 0


@pytest.mark.asyncio
async def test_llm_refine_branch_merges_results_without_overwriting_regex_typed_entries():
    state_machine = ProtocolStateMachine(
        name="TCP Main",
        states=[ProtocolState(name="LISTEN", is_initial=True), ProtocolState(name="ESTABLISHED")],
        transitions=[
            ProtocolTransition(
                from_state="LISTEN",
                to_state="ESTABLISHED",
                event="SEGMENT_ARRIVAL",
                condition="state == ESTABLISHED",
                actions=["start retransmit timer", "advance SND.UNA"],
            )
        ],
    )
    schema = ProtocolSchema(
        protocol_name="rfc793-TCP",
        state_machines=[state_machine],
        timers=[TimerConfig(timer_name="Retransmit Timer")],
    )
    fsm_ir = lower_state_machine_to_fsm_ir(state_machine, schema.protocol_name)
    hint = build_protocol_hint(schema, [fsm_ir])
    block = fsm_ir.blocks[0]
    branch = block.branches[0]

    refined_branch, accepted_guard_count, accepted_action_count = await _llm_refine_branch(
        fsm_ir.name,
        schema.protocol_name,
        block,
        branch,
        StaticRefineLLM(
            {
                "guard": {
                    "kind": "always",
                    "description": "ignored because regex guard already exists",
                },
                "actions": [
                    {
                        "kind": "update_field",
                        "ref_source": "ctx",
                        "target": "SND.UNA",
                        "value": "0",
                    }
                ],
            }
        ),
        hint,
    )

    assert accepted_guard_count == 0
    assert accepted_action_count == 1
    assert refined_branch.guard_typed == branch.guard_typed
    assert len(refined_branch.actions_typed) == 2
    assert refined_branch.actions_typed[0].kind == "start_timer"
    assert refined_branch.actions_typed[1].kind == "update_field"
    assert refined_branch.actions_raw == []
    assert "llm_refined_actions:1" in refined_branch.notes


@pytest.mark.asyncio
async def test_refine_fsm_irs_updates_stats():
    needs_refine = ProtocolStateMachine(
        name="TCP Main",
        states=[
            ProtocolState(name="LISTEN", is_initial=True),
            ProtocolState(name="SYN_RCVD"),
            ProtocolState(name="ESTABLISHED"),
        ],
        transitions=[
            ProtocolTransition(
                from_state="LISTEN",
                to_state="SYN_RCVD",
                event="SEGMENT_ARRIVAL",
                condition="SEG.ACK is zero",
                actions=["advance SND.UNA"],
            ),
            ProtocolTransition(
                from_state="LISTEN",
                to_state="ESTABLISHED",
                event="SEGMENT_ARRIVAL",
                condition="Always",
                actions=["arm Retransmit Timer"],
            ),
            ProtocolTransition(
                from_state="SYN_RCVD",
                to_state="ESTABLISHED",
                event="ACK",
                actions=["start retransmit timer"],
            ),
        ],
    )
    small_fsm = ProtocolStateMachine(
        name="Small FSM",
        states=[ProtocolState(name="A", is_initial=True), ProtocolState(name="B")],
        transitions=[
            ProtocolTransition(from_state="A", to_state="B", event="E1"),
            ProtocolTransition(from_state="B", to_state="A", event="E2"),
            ProtocolTransition(from_state="A", to_state="B", event="E3", condition="complex raw guard"),
        ],
    )
    schema = ProtocolSchema(
        protocol_name="rfc793-TCP",
        state_machines=[needs_refine, small_fsm],
        timers=[TimerConfig(timer_name="Retransmit Timer")],
    )
    fsm_irs = [
        lower_state_machine_to_fsm_ir(needs_refine, schema.protocol_name),
        lower_state_machine_to_fsm_ir(small_fsm, schema.protocol_name),
    ]

    refined_fsm_irs, stats = await refine_fsm_irs(
        fsm_irs,
        schema,
        SequenceRefineLLM(
            [
                {
                    "guard": {
                        "kind": "context_field_eq",
                        "ref_source": "ctx",
                        "field_ref": "SEG.ACK",
                        "operator": "==",
                        "value": "0",
                    },
                    "actions": [
                        {
                            "kind": "update_field",
                            "ref_source": "ctx",
                            "target": "SND.UNA",
                            "value": "0",
                        }
                    ],
                },
                {
                    "guard": {"kind": "always"},
                    "actions": [
                        {
                            "kind": "start_timer",
                            "target": "Retransmit Timer",
                        }
                    ],
                },
            ]
        ),
    )

    assert len(refined_fsm_irs) == 2
    assert stats.triggered_count == 1
    assert stats.accepted_guard_count == 2
    assert stats.accepted_action_count == 2
    assert stats.raw_branch_ratio_before > stats.raw_branch_ratio_after
    assert refined_fsm_irs[0].normalization_status == NormalizationStatus.DEGRADED_READY
