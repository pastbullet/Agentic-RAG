"""Tests for Phase B alignment reports and validator."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.extract.fsm_ir import lower_state_machine_to_fsm_ir
from src.extract.state_context_alignment import (
    validate_all_fsm_context_alignments,
    validate_fsm_context_alignment,
)
from src.extract.state_context_materializer import materialize_protocol_state_context
from src.models import (
    AlignmentFSMReport,
    AlignmentReport,
    AlignmentSummary,
    ContextFieldIR,
    ContextTimerIR,
    IRDiagnostic,
    ProtocolSchema,
    ProtocolState,
    ProtocolStateMachine,
    ProtocolTransition,
    StateContextIR,
    TimerConfig,
)


def _make_fsm_ir(
    *,
    protocol_name: str = "rfc5880-BFD",
    name: str = "BFD Session State Machine",
    transitions: list[ProtocolTransition] | None = None,
):
    state_machine = ProtocolStateMachine(
        name=name,
        states=[
            ProtocolState(name="Down", is_initial=True),
            ProtocolState(name="Init"),
            ProtocolState(name="Up", is_final=True),
        ],
        transitions=transitions or [],
    )
    return lower_state_machine_to_fsm_ir(state_machine, protocol_name)


def test_alignment_report_roundtrip():
    report = AlignmentReport(
        protocol_name="rfc793-TCP",
        context_id="rfc793_tcp_context",
        summary=AlignmentSummary(
            fsm_count=2,
            error_count=1,
            warning_count=2,
            aligned_fsm_count=1,
            typed_ref_count=8,
            resolved_ref_count=6,
            coverage_ratio=0.75,
        ),
        fsm_reports=[
            AlignmentFSMReport(
                fsm_ir_id="tcp_connection_state_machine",
                fsm_name="TCP Connection State Machine",
                error_count=1,
                warning_count=0,
                typed_ref_count=4,
                resolved_ref_count=3,
                diagnostics=[
                    IRDiagnostic(
                        level="error",
                        code="FSM_CTX_FIELD_MISSING",
                        message="Missing send_window.",
                    )
                ],
            )
        ],
        context_diagnostics=[
            IRDiagnostic(
                level="warning",
                code="CTX_FIELD_UNREFERENCED",
                message="Unused field.",
            )
        ],
    )

    restored = AlignmentReport.model_validate_json(report.model_dump_json())

    assert restored == report


@pytest.mark.parametrize("coverage_ratio", [-0.1, 1.1])
def test_alignment_summary_rejects_out_of_range_coverage_ratio(coverage_ratio: float):
    with pytest.raises(ValidationError):
        AlignmentSummary(coverage_ratio=coverage_ratio)


def test_alignment_summary_allows_zero_typed_refs_with_zero_coverage_ratio():
    summary = AlignmentSummary(
        fsm_count=1,
        error_count=0,
        warning_count=0,
        aligned_fsm_count=1,
        typed_ref_count=0,
        resolved_ref_count=0,
        coverage_ratio=0.0,
    )

    assert summary.coverage_ratio == 0.0


def test_validate_fsm_context_alignment_reports_missing_guard_field():
    fsm_ir = _make_fsm_ir(
        transitions=[
            ProtocolTransition(
                from_state="Down",
                to_state="Init",
                event="RECV",
                condition="ctx.counter == 1",
            )
        ]
    )
    context = StateContextIR(
        context_id="bfd_context",
        name="BFD Context",
        canonical_name="bfd_context",
        scope="session",
        state_field="session_state",
        fields=[
            ContextFieldIR(
                field_id="bfd.session_state",
                name="Session State",
                canonical_name="session_state",
                type_kind="enum",
                semantic_role="state",
            )
        ],
        timers=[],
        resources=[],
        invariants=[],
    )

    report = validate_fsm_context_alignment(fsm_ir, context)

    assert report.error_count == 1
    assert report.typed_ref_count == 1
    assert report.resolved_ref_count == 0
    assert [diag.code for diag in report.diagnostics] == ["FSM_CTX_FIELD_MISSING"]


def test_validate_fsm_context_alignment_reports_missing_update_timer_and_state_field():
    fsm_ir = _make_fsm_ir(
        transitions=[
            ProtocolTransition(
                from_state="Down",
                to_state="Up",
                event="TIMEOUT",
                actions=[
                    "set ctx.counter to 1",
                    "start hold timer",
                    "set state to Up",
                ],
            )
        ]
    )
    context = StateContextIR(
        context_id="bfd_context",
        name="BFD Context",
        canonical_name="bfd_context",
        scope="session",
        state_field="counter",
        fields=[
            ContextFieldIR(
                field_id="bfd.counter",
                name="Counter",
                canonical_name="counter",
                type_kind="u32",
                semantic_role="send_next_seq",
            )
        ],
        timers=[],
        resources=[],
        invariants=[],
    )

    report = validate_fsm_context_alignment(fsm_ir, context)

    assert report.error_count == 2
    assert report.typed_ref_count == 3
    assert report.resolved_ref_count == 1
    assert {diag.code for diag in report.diagnostics} == {
        "FSM_CTX_TIMER_ACTION_MISSING",
        "CTX_STATE_FIELD_MISSING",
    }


def test_validate_all_fsm_context_alignments_summarizes_coverage_and_unreferenced_context():
    fsm_ir = _make_fsm_ir(
        transitions=[
            ProtocolTransition(
                from_state="Down",
                to_state="Init",
                event="RECV",
                condition="ctx.counter == 1",
            )
        ]
    )
    context = StateContextIR(
        context_id="bfd_context",
        name="BFD Context",
        canonical_name="bfd_context",
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
                field_id="bfd.counter",
                name="Counter",
                canonical_name="counter",
                type_kind="u32",
            ),
            ContextFieldIR(
                field_id="bfd.unused",
                name="Unused",
                canonical_name="unused",
                type_kind="u32",
            ),
        ],
        timers=[
            ContextTimerIR(
                timer_id="bfd.unused_timer",
                name="Unused Timer",
                canonical_name="unused_timer",
            )
        ],
        resources=[],
        invariants=[],
    )

    report = validate_all_fsm_context_alignments([fsm_ir], context)

    assert report.summary.fsm_count == 1
    assert report.summary.error_count == 0
    assert report.summary.warning_count == 2
    assert report.summary.typed_ref_count == 1
    assert report.summary.resolved_ref_count == 1
    assert report.summary.coverage_ratio == 1.0
    assert {diag.code for diag in report.context_diagnostics} == {
        "CTX_FIELD_UNREFERENCED",
        "CTX_TIMER_UNREFERENCED",
    }


def test_validate_all_fsm_context_alignments_real_bfd_schema_has_no_errors():
    schema = ProtocolSchema.model_validate_json(Path("data/out/rfc5880-BFD/protocol_schema.json").read_text())
    schema.fsm_irs = [lower_state_machine_to_fsm_ir(sm, schema.protocol_name) for sm in schema.state_machines]
    context = materialize_protocol_state_context(schema)

    report = validate_all_fsm_context_alignments(schema.fsm_irs, context)

    assert report.summary.error_count == 0
    assert report.summary.typed_ref_count >= report.summary.resolved_ref_count


def test_validate_all_fsm_context_alignments_real_tcp_schema_has_no_errors():
    schema = ProtocolSchema.model_validate_json(Path("data/out/rfc793-TCP/protocol_schema.json").read_text())
    schema.fsm_irs = [lower_state_machine_to_fsm_ir(sm, schema.protocol_name) for sm in schema.state_machines]
    context = materialize_protocol_state_context(schema)

    report = validate_all_fsm_context_alignments(schema.fsm_irs, context)

    assert report.summary.error_count == 0
    assert report.summary.typed_ref_count >= report.summary.resolved_ref_count


def test_validate_all_fsm_context_alignments_without_fsm_uses_context_name_fallback():
    context = StateContextIR(
        context_id="demo_context",
        name="Demo Context",
        canonical_name="demo_context",
        scope="session",
        state_field=None,
        fields=[],
        timers=[],
        resources=[],
        invariants=[],
    )

    report = validate_all_fsm_context_alignments([], context)

    assert report.protocol_name == "Demo Context"
