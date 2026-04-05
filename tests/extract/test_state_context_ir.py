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
    assert [diag.code for diag in normalized.diagnostics] == ["state_field_inferred"]


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


# ── Consumer-driven readiness ──────────────────────────────────


def test_minimal_bfd_context_without_tcp_roles_is_ready():
    """A BFD-like context with only state + BFD-specific fields should be READY,
    not blocked by missing TCP roles like send_next_seq."""
    context = StateContextIR(
        context_id="bfd_session_context",
        name="BFD Session Context",
        canonical_name="bfd_session_context",
        scope="session",
        state_field="session_state",
        fields=[
            ContextFieldIR(
                field_id="bfd.session_state",
                name="Session State",
                canonical_name="session_state",
                type_kind="enum",
                semantic_role="state",
            ),
            ContextFieldIR(
                field_id="bfd.local_discr",
                name="Local Discriminator",
                canonical_name="local_discr",
                type_kind="u32",
                width_bits=32,
            ),
            ContextFieldIR(
                field_id="bfd.desired_min_tx",
                name="Desired Min TX Interval",
                canonical_name="desired_min_tx",
                type_kind="u32",
                width_bits=32,
            ),
        ],
        timers=[
            ContextTimerIR(
                timer_id="bfd.detect_timer",
                name="Detection Timer",
                canonical_name="detect_timer",
                semantic_role="hold_timer",
            ),
        ],
        resources=[],
        invariants=[],
    )

    normalized = normalize_state_context_ir(context)

    # Without TCP-specific role requirement, a BFD context with
    # state + fields + timer should not be BLOCKED.
    assert normalized.readiness != NormalizationStatus.BLOCKED
    assert normalized.state_field == "session_state"


def test_consumer_driven_readiness_detects_missing_refs():
    """When FSM consumer refs are provided, missing slots degrade readiness."""
    context = StateContextIR(
        context_id="test_ctx",
        name="Test Context",
        canonical_name="test_ctx",
        scope="session",
        state_field="state",
        fields=[
            ContextFieldIR(
                field_id="test.state",
                name="State",
                canonical_name="state",
                type_kind="enum",
                semantic_role="state",
            ),
        ],
        timers=[],
        resources=[],
        invariants=[],
    )

    # FSM references "counter" which is not in the context
    normalized = normalize_state_context_ir(context, required_refs={"state", "counter"})

    assert normalized.readiness == NormalizationStatus.DEGRADED_READY
    assert any(d.code == "missing_consumer_refs" for d in normalized.diagnostics)
    assert "counter" in next(
        d.message for d in normalized.diagnostics if d.code == "missing_consumer_refs"
    )


def test_state_context_blocks_when_state_field_cannot_be_inferred():
    context = StateContextIR(
        context_id="test_ctx",
        name="Test Context",
        canonical_name="test_ctx",
        scope="session",
        state_field=None,
        fields=[
            ContextFieldIR(
                field_id="test.counter",
                name="Counter",
                canonical_name="counter",
                type_kind="u32",
            ),
        ],
        timers=[],
        resources=[],
        invariants=[],
    )

    normalized = normalize_state_context_ir(context)

    assert normalized.readiness == NormalizationStatus.BLOCKED
    assert any(d.code == "missing_state_field" for d in normalized.diagnostics)


def test_state_context_blocks_on_invalid_state_field_name():
    context = StateContextIR(
        context_id="test_ctx",
        name="Test Context",
        canonical_name="test_ctx",
        scope="session",
        state_field="missing_state",
        fields=[
            ContextFieldIR(
                field_id="test.session_state",
                name="Session State",
                canonical_name="session_state",
                type_kind="enum",
                semantic_role="state",
            ),
        ],
        timers=[],
        resources=[],
        invariants=[],
    )

    normalized = normalize_state_context_ir(context)

    assert normalized.readiness == NormalizationStatus.BLOCKED
    assert any(d.code == "invalid_state_field" for d in normalized.diagnostics)


def test_state_context_blocks_on_state_field_role_mismatch():
    context = StateContextIR(
        context_id="test_ctx",
        name="Test Context",
        canonical_name="test_ctx",
        scope="session",
        state_field="counter",
        fields=[
            ContextFieldIR(
                field_id="test.counter",
                name="Counter",
                canonical_name="counter",
                type_kind="u32",
                semantic_role="send_next_seq",
            ),
        ],
        timers=[],
        resources=[],
        invariants=[],
    )

    normalized = normalize_state_context_ir(context)

    assert normalized.readiness == NormalizationStatus.BLOCKED
    assert any(d.code == "state_field_role_mismatch" for d in normalized.diagnostics)


# ── Provenance tracking ──────────────────────────────────


def test_provenance_field_serialization():
    """Provenance tags survive JSON roundtrip."""
    field = ContextFieldIR(
        field_id="test.counter",
        name="Counter",
        canonical_name="counter",
        type_kind="u32",
        provenance=["fsm_ref", "document_clue"],
    )
    restored = ContextFieldIR.model_validate_json(field.model_dump_json())
    assert restored.provenance == ["fsm_ref", "document_clue"]


def test_provenance_timer_serialization():
    timer = ContextTimerIR(
        timer_id="test.retransmit",
        name="Retransmit",
        canonical_name="retransmit",
        provenance=["manual_patch"],
    )
    restored = ContextTimerIR.model_validate_json(timer.model_dump_json())
    assert restored.provenance == ["manual_patch"]
