"""Tests for StateContextIR example fixtures."""

from __future__ import annotations

from src.extract.state_context import build_generic_session_state_context, build_tcp_connection_state_context
from src.models import NormalizationStatus


def test_tcp_connection_state_context_example_is_ready():
    context = build_tcp_connection_state_context()

    assert context.scope == "connection"
    assert context.readiness == NormalizationStatus.READY
    assert context.name == "TCP Connection Context"
    assert context.state_field == "connection_state"
    assert [field.semantic_role for field in context.fields] == [
        "state",
        "send_next_seq",
        "recv_next_seq",
        "send_window",
        "recv_window",
    ]
    assert context.timers[0].semantic_role == "retransmission_timer"
    assert context.resources[0].semantic_role == "send_queue"
    assert context.resources[0].kind == "queue"


def test_generic_session_state_context_example_is_not_tcp_only():
    context = build_generic_session_state_context()

    assert context.scope == "session"
    assert context.readiness == NormalizationStatus.DEGRADED_READY
    assert context.name == "Generic Session Context"
    assert context.canonical_name == "generic_session_context"
    assert context.state_field == "session_state"
    assert "tcp" not in context.canonical_name
    assert [field.semantic_role for field in context.fields] == [
        "state",
        "send_next_seq",
        "recv_next_seq",
        "send_window",
        "recv_window",
    ]
