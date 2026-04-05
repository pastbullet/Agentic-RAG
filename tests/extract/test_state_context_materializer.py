"""Tests for Phase A StateContextIR materialization."""

from __future__ import annotations

from pathlib import Path

from src.extract.fsm_ir import lower_state_machine_to_fsm_ir
from src.extract.state_context_materializer import (
    _merge_field,
    _merge_resource,
    _merge_timer,
    canonicalize_context_name,
    collect_document_clues,
    collect_fsm_refs,
    load_context_patch,
    materialize_protocol_state_context,
)
from src.models import (
    ContextFieldIR,
    ContextPatch,
    ContextResourceIR,
    ContextTimerIR,
    NormalizationStatus,
    ProtocolSchema,
    ProtocolState,
    ProtocolStateMachine,
    ProtocolTransition,
    TimerConfig,
)


def _make_schema(
    *,
    protocol_name: str = "rfc5880-BFD",
    sm_name: str = "BFD Session State Machine",
    transitions: list[ProtocolTransition] | None = None,
    timers: list[TimerConfig] | None = None,
) -> ProtocolSchema:
    state_machine = ProtocolStateMachine(
        name=sm_name,
        states=[
            ProtocolState(name="Down", is_initial=True),
            ProtocolState(name="Init"),
            ProtocolState(name="Up", is_final=True),
        ],
        transitions=transitions or [],
        source_pages=[1],
    )
    schema = ProtocolSchema(
        protocol_name=protocol_name,
        source_document=f"{protocol_name}.pdf",
        state_machines=[state_machine],
        timers=timers or [],
    )
    schema.fsm_irs = [lower_state_machine_to_fsm_ir(state_machine, protocol_name)]
    return schema


def test_collect_fsm_refs_extracts_ctx_fields():
    schema = _make_schema(
        transitions=[
            ProtocolTransition(
                from_state="Down",
                to_state="Init",
                event="RECV",
                condition="ctx.session_state == Down",
                actions=["set ctx.counter to 1"],
            ),
        ]
    )

    refs = collect_fsm_refs(schema.fsm_irs, schema.protocol_name)

    assert sorted(refs.ctx_fields) == ["counter", "session_state"]
    assert refs.required_refs == {"counter", "session_state"}


def test_collect_fsm_refs_extracts_timer_refs():
    schema = _make_schema(
        transitions=[
            ProtocolTransition(
                from_state="Down",
                to_state="Init",
                event="DETECT_TIMEOUT",
                condition="holddown expires",
                actions=["start detect timer", "cancel detect timer"],
            ),
        ]
    )

    refs = collect_fsm_refs(schema.fsm_irs, schema.protocol_name)

    assert "holddown" in refs.timers
    assert "detect" in refs.timers
    assert refs.required_refs == {"detect", "holddown"}


def test_set_state_only_marks_state_presence_without_creating_state_value_field():
    schema = _make_schema(
        protocol_name="rfc793-TCP",
        sm_name="TCP Connection State Machine",
        transitions=[
            ProtocolTransition(
                from_state="Down",
                to_state="Init",
                event="OPEN",
                actions=["set state to ESTABLISHED"],
            ),
        ],
    )

    refs = collect_fsm_refs(schema.fsm_irs, schema.protocol_name)
    context = materialize_protocol_state_context(schema)

    assert refs.has_set_state is True
    assert "established" not in refs.ctx_fields
    assert context.state_field == "connection_state"


def test_collect_document_clues_timers_and_scope():
    schema = _make_schema(
        transitions=[],
        timers=[TimerConfig(timer_name="Detection Time", timeout_value="3 x interval")],
    )

    clues = collect_document_clues(schema)

    assert clues.inferred_scope == "session"
    assert "detection_time" in clues.timers
    assert clues.timers["detection_time"].duration_expr == "3 x interval"


def test_canonical_name_normalization():
    assert canonicalize_context_name("bfd.SessionState", "rfc5880-BFD") == "session_state"
    assert canonicalize_context_name("SND.NXT", "rfc793-TCP") == "send_next_seq"
    assert canonicalize_context_name("SND.UP", "rfc793-TCP") == "send_urgent_ptr"
    assert canonicalize_context_name("IRS", "rfc793-TCP") == "initial_recv_seq"
    assert canonicalize_context_name("SEG.PRC", "rfc793-TCP") == "segment_precedence"
    assert canonicalize_context_name("ACK", "rfc793-TCP") == ""


def test_merge_patch_overrides_role(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    patch_dir = Path("data/patches/rfc5880-BFD")
    patch_dir.mkdir(parents=True, exist_ok=True)
    (patch_dir / "context_patch.json").write_text(
        """{
  "role_overrides": {
    "bfd.LocalDiag": "state"
  }
}""",
        encoding="utf-8",
    )

    schema = _make_schema(
        transitions=[
            ProtocolTransition(
                from_state="Down",
                to_state="Init",
                event="RECV",
                condition="bfd.LocalDiag == 1",
            ),
        ]
    )
    patch = load_context_patch(schema.protocol_name)

    assert isinstance(patch, ContextPatch)
    context = materialize_protocol_state_context(schema, patch)
    roles = {field.canonical_name: field.semantic_role for field in context.fields}
    assert roles["local_diag"] == "state"


def test_merge_deduplicates_same_name_and_unions_provenance():
    schema = _make_schema(
        protocol_name="rfc793-TCP",
        sm_name="TCP Connection State Machine",
        transitions=[
            ProtocolTransition(
                from_state="Down",
                to_state="Init",
                event="RTO",
                condition="retransmission_timer expires",
                actions=["start retransmission timer"],
            ),
        ],
        timers=[TimerConfig(timer_name="Retransmission Timer", timeout_value="RTO")],
    )

    context = materialize_protocol_state_context(schema)
    timer = next(item for item in context.timers if item.canonical_name == "retransmission_timer")

    assert timer.provenance == ["fsm_ref", "document_clue"]


def test_merge_field_patch_wins_and_provenance_unions():
    merged: dict[str, ContextFieldIR] = {}
    diagnostics = []

    _merge_field(
        merged,
        ContextFieldIR(
            field_id="ctx.counter",
            name="Counter",
            canonical_name="counter",
            type_kind="opaque",
            provenance=["fsm_ref"],
        ),
        "fsm_ref",
        diagnostics,
    )
    _merge_field(
        merged,
        ContextFieldIR(
            field_id="ctx.counter",
            name="Counter",
            canonical_name="counter",
            type_kind="u16",
            width_bits=16,
            semantic_role="recv_window",
            provenance=["document_clue"],
        ),
        "document_clue",
        diagnostics,
    )
    _merge_field(
        merged,
        ContextFieldIR(
            field_id="ctx.counter",
            name="Counter",
            canonical_name="counter",
            type_kind="u32",
            width_bits=32,
            semantic_role="send_next_seq",
            provenance=["manual_patch"],
        ),
        "manual_patch",
        diagnostics,
    )

    field = merged["counter"]
    assert field.type_kind == "u32"
    assert field.width_bits == 32
    assert field.semantic_role == "send_next_seq"
    assert field.provenance == ["fsm_ref", "document_clue", "manual_patch"]


def test_merge_field_lower_priority_cannot_override_higher_priority():
    merged: dict[str, ContextFieldIR] = {
        "counter": ContextFieldIR(
            field_id="ctx.counter",
            name="Counter",
            canonical_name="counter",
            type_kind="u32",
            width_bits=32,
            semantic_role="send_next_seq",
            provenance=["manual_patch"],
        )
    }
    diagnostics = []

    _merge_field(
        merged,
        ContextFieldIR(
            field_id="ctx.counter",
            name="Counter",
            canonical_name="counter",
            type_kind="u16",
            width_bits=16,
            semantic_role="recv_window",
            provenance=["document_clue"],
        ),
        "document_clue",
        diagnostics,
    )

    field = merged["counter"]
    assert field.type_kind == "u32"
    assert field.width_bits == 32
    assert field.semantic_role == "send_next_seq"
    assert any(diag.code == "CTX_MERGE_CONFLICT" for diag in diagnostics)


def test_merge_field_same_priority_conflict_keeps_existing_value():
    merged: dict[str, ContextFieldIR] = {
        "counter": ContextFieldIR(
            field_id="ctx.counter",
            name="Counter",
            canonical_name="counter",
            type_kind="u16",
            width_bits=16,
            provenance=["document_clue"],
        )
    }
    diagnostics = []

    _merge_field(
        merged,
        ContextFieldIR(
            field_id="ctx.counter",
            name="Counter",
            canonical_name="counter",
            type_kind="u32",
            width_bits=32,
            provenance=["document_clue"],
        ),
        "document_clue",
        diagnostics,
    )

    field = merged["counter"]
    assert field.type_kind == "u16"
    assert field.width_bits == 16
    assert any(diag.code == "CTX_MERGE_CONFLICT" for diag in diagnostics)


def test_merge_timer_lower_priority_cannot_override_higher_priority():
    merged: dict[str, ContextTimerIR] = {
        "hold_timer": ContextTimerIR(
            timer_id="ctx.hold_timer",
            name="Hold Timer",
            canonical_name="hold_timer",
            semantic_role="hold_timer",
            duration_expr="patch_timeout",
            provenance=["manual_patch"],
        )
    }
    diagnostics = []

    _merge_timer(
        merged,
        ContextTimerIR(
            timer_id="ctx.hold_timer",
            name="Hold Timer",
            canonical_name="hold_timer",
            semantic_role="keepalive",
            duration_expr="fsm_timeout",
            provenance=["fsm_ref"],
        ),
        "fsm_ref",
        diagnostics,
    )

    timer = merged["hold_timer"]
    assert timer.semantic_role == "hold_timer"
    assert timer.duration_expr == "patch_timeout"
    assert timer.provenance == ["manual_patch", "fsm_ref"]


def test_merge_resource_lower_priority_cannot_override_higher_priority():
    merged: dict[str, ContextResourceIR] = {
        "send_queue": ContextResourceIR(
            resource_id="ctx.send_queue",
            name="Send Queue",
            canonical_name="send_queue",
            kind="queue",
            semantic_role="send_queue",
            element_kind="segment_ref",
            provenance=["manual_patch"],
        )
    }
    diagnostics = []

    _merge_resource(
        merged,
        ContextResourceIR(
            resource_id="ctx.send_queue",
            name="Send Queue",
            canonical_name="send_queue",
            kind="buffer",
            semantic_role="retransmission_queue",
            element_kind="bytes",
            provenance=["fsm_ref"],
        ),
        "fsm_ref",
        diagnostics,
    )

    resource = merged["send_queue"]
    assert resource.kind == "queue"
    assert resource.semantic_role == "send_queue"
    assert resource.element_kind == "segment_ref"
    assert resource.provenance == ["manual_patch", "fsm_ref"]


def test_materialize_empty_typed_refs_uses_document_clues_for_minimal_context():
    schema = _make_schema(
        transitions=[],
        timers=[TimerConfig(timer_name="Detection Time", timeout_value="3 x interval")],
    )

    context = materialize_protocol_state_context(schema)

    assert context.state_field == "session_state"
    assert context.readiness == NormalizationStatus.READY
    assert any(timer.canonical_name == "detection_time" for timer in context.timers)


def test_readiness_consumer_driven_full_coverage_is_ready():
    schema = _make_schema(
        transitions=[
            ProtocolTransition(
                from_state="Down",
                to_state="Init",
                event="RECV",
                condition="ctx.counter == 0",
            ),
        ]
    )
    context = materialize_protocol_state_context(schema)

    assert context.readiness == NormalizationStatus.READY
    assert all(diag.code != "missing_consumer_refs" for diag in context.diagnostics)


def test_resource_only_comes_from_patch():
    schema = _make_schema(transitions=[])
    patch = ContextPatch.model_validate(
        {
            "extra_resources": [
                {
                    "canonical_name": "send_queue",
                    "kind": "queue",
                    "semantic_role": "send_queue",
                    "element_kind": "segment_ref",
                }
            ]
        }
    )

    context = materialize_protocol_state_context(schema, patch)

    assert [resource.canonical_name for resource in context.resources] == ["send_queue"]
    assert context.resources[0].provenance == ["manual_patch"]


def test_bfd_real_schema_materializes():
    schema = ProtocolSchema.model_validate_json(Path("data/out/rfc5880-BFD/protocol_schema.json").read_text())
    schema.fsm_irs = [lower_state_machine_to_fsm_ir(sm, schema.protocol_name) for sm in schema.state_machines]

    context = materialize_protocol_state_context(schema)

    assert context.scope == "session"
    assert context.state_field is not None
    assert len(context.timers) >= 1


def test_tcp_real_schema_materializes():
    schema = ProtocolSchema.model_validate_json(Path("data/out/rfc793-TCP/protocol_schema.json").read_text())
    schema.fsm_irs = [lower_state_machine_to_fsm_ir(sm, schema.protocol_name) for sm in schema.state_machines]

    context = materialize_protocol_state_context(schema)

    assert context.scope == "connection"
    assert context.state_field is not None
    assert len(context.timers) >= 1


def test_tcp_real_schema_materializes_with_patch_and_filters_noise():
    schema = ProtocolSchema.model_validate_json(Path("data/out/rfc793-TCP/protocol_schema.json").read_text())
    schema.fsm_irs = [lower_state_machine_to_fsm_ir(sm, schema.protocol_name) for sm in schema.state_machines]

    patch = load_context_patch(schema.protocol_name)
    context = materialize_protocol_state_context(schema, patch)
    field_names = {field.canonical_name for field in context.fields}
    timer_names = {timer.canonical_name for timer in context.timers}
    resource_names = {resource.canonical_name for resource in context.resources}

    assert patch is not None
    assert "ack" not in field_names
    assert "bit" not in field_names
    assert "control" not in field_names
    assert "timeout" not in field_names
    assert "the" not in timer_names
    assert "send_window" in field_names
    assert "recv_window" in field_names
    assert "send_window_update_seq" in field_names
    assert "send_window_update_ack" in field_names
    assert "initial_send_seq" in field_names
    assert "initial_recv_seq" in field_names
    assert "send_urgent_ptr" in field_names
    assert "time_wait_timer" in timer_names
    assert "retransmission_queue" in resource_names
