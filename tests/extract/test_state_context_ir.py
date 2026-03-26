"""Tests for StateContextIR normalization and readiness."""

from __future__ import annotations

from src.extract.state_context import build_tcp_connection_state_context, normalize_state_context_ir
from src.models import (
    ContextFieldIR,
    ContextResourceIR,
    ContextTimerIR,
    NormalizationStatus,
    StateContextIR,
)


def test_tcp_connection_state_context_normalizes_to_ready():
    context = build_tcp_connection_state_context()

    assert context.readiness == NormalizationStatus.READY
    assert context.scope == "connection"
    assert context.state_field == "connection_state"
    assert [field.semantic_role for field in context.fields] == [
        "state",
        "send_next_seq",
        "recv_next_seq",
        "send_window",
        "recv_window",
    ]
    assert [timer.semantic_role for timer in context.timers] == ["retransmission_timer"]
    assert [resource.semantic_role for resource in context.resources] == ["send_queue"]
    assert context.diagnostics == []


def test_state_context_infers_state_field_and_degrades_when_timer_resource_missing():
    context = StateContextIR(
        context_id="generic_session_context",
        name="Generic Session Context",
        canonical_name="generic_session_context",
        scope="session",
        state_field=None,
        fields=[
            ContextFieldIR(
                field_id="generic_session_context.session_state",
                name="Session State",
                canonical_name="session_state",
                type_kind="enum",
                semantic_role="state",
            ),
            ContextFieldIR(
                field_id="generic_session_context.send_next_seq",
                name="Send Next Sequence",
                canonical_name="send_next_seq",
                type_kind="u32",
                width_bits=32,
                semantic_role="send_next_seq",
            ),
            ContextFieldIR(
                field_id="generic_session_context.recv_next_seq",
                name="Receive Next Sequence",
                canonical_name="recv_next_seq",
                type_kind="u32",
                width_bits=32,
                semantic_role="recv_next_seq",
            ),
            ContextFieldIR(
                field_id="generic_session_context.send_window",
                name="Send Window",
                canonical_name="send_window",
                type_kind="u16",
                width_bits=16,
                semantic_role="send_window",
            ),
            ContextFieldIR(
                field_id="generic_session_context.recv_window",
                name="Receive Window",
                canonical_name="recv_window",
                type_kind="u16",
                width_bits=16,
                semantic_role="recv_window",
            ),
        ],
        timers=[],
        resources=[],
        invariants=[],
    )

    normalized = normalize_state_context_ir(context)

    assert normalized.readiness == NormalizationStatus.DEGRADED_READY
    assert normalized.state_field == "session_state"
    assert [diag.code for diag in normalized.diagnostics] == [
        "state_field_inferred",
        "missing_timer_context",
        "missing_resource_context",
    ]


def test_state_context_blocks_on_invalid_scope():
    context = StateContextIR(
        context_id="broken_context",
        name="Broken Context",
        canonical_name="broken_context",
        scope="protocol",
        state_field="state",
        fields=[
            ContextFieldIR(
                field_id="broken_context.state",
                name="State",
                canonical_name="state",
                type_kind="enum",
                semantic_role="state",
            ),
            ContextFieldIR(
                field_id="broken_context.send_next_seq",
                name="Send Next Sequence",
                canonical_name="send_next_seq",
                type_kind="u32",
                width_bits=32,
                semantic_role="send_next_seq",
            ),
            ContextFieldIR(
                field_id="broken_context.recv_next_seq",
                name="Receive Next Sequence",
                canonical_name="recv_next_seq",
                type_kind="u32",
                width_bits=32,
                semantic_role="recv_next_seq",
            ),
            ContextFieldIR(
                field_id="broken_context.send_window",
                name="Send Window",
                canonical_name="send_window",
                type_kind="u16",
                width_bits=16,
                semantic_role="send_window",
            ),
            ContextFieldIR(
                field_id="broken_context.recv_window",
                name="Receive Window",
                canonical_name="recv_window",
                type_kind="u16",
                width_bits=16,
                semantic_role="recv_window",
            ),
        ],
        timers=[
            ContextTimerIR(
                timer_id="broken_context.retransmission_timer",
                name="Retransmission Timer",
                canonical_name="retransmission_timer",
                semantic_role="retransmission_timer",
                duration_source_kind="derived",
                duration_expr="retransmission_timeout",
                triggers_event="retransmission_timeout_expires",
            )
        ],
        resources=[
            ContextResourceIR(
                resource_id="broken_context.send_queue",
                name="Send Queue",
                canonical_name="send_queue",
                kind="queue",
                semantic_role="send_queue",
                element_kind="segment_ref",
            )
        ],
        invariants=[],
    )

    normalized = normalize_state_context_ir(context)

    assert normalized.readiness == NormalizationStatus.BLOCKED
    assert any(diag.code == "invalid_scope" for diag in normalized.diagnostics)
    restored = StateContextIR.model_validate_json(normalized.model_dump_json())
    assert restored == normalized
