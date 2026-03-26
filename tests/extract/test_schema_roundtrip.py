"""Round-trip tests for protocol extraction schemas."""

from __future__ import annotations

import json

from hypothesis import given, settings
from hypothesis import strategies as st

from src.models import (
    ErrorRule,
    NodeLabelMeta,
    NodeSemanticLabel,
    ProcedureRule,
    ProcedureStep,
    ProtocolField,
    ProtocolMessage,
    ProtocolSchema,
    ProtocolState,
    ProtocolStateMachine,
    ProtocolTransition,
    TimerConfig,
)
from src.extract.state_context import build_generic_session_state_context, build_tcp_connection_state_context


state_st = st.builds(
    ProtocolState,
    name=st.text(min_size=1, max_size=40),
    description=st.text(max_size=80),
    is_initial=st.booleans(),
    is_final=st.booleans(),
)

transition_st = st.builds(
    ProtocolTransition,
    from_state=st.text(min_size=1, max_size=40),
    to_state=st.text(min_size=1, max_size=40),
    event=st.text(min_size=1, max_size=60),
    condition=st.text(max_size=80),
    actions=st.lists(st.text(max_size=40), max_size=5),
)

state_machine_st = st.builds(
    ProtocolStateMachine,
    name=st.text(min_size=1, max_size=60),
    states=st.lists(state_st, max_size=6),
    transitions=st.lists(transition_st, max_size=10),
    source_pages=st.lists(st.integers(min_value=1, max_value=500), max_size=10),
)

field_st = st.builds(
    ProtocolField,
    name=st.text(min_size=1, max_size=40),
    type=st.text(max_size=20),
    size_bits=st.one_of(st.none(), st.integers(min_value=0, max_value=2048)),
    description=st.text(max_size=80),
)

message_st = st.builds(
    ProtocolMessage,
    name=st.text(min_size=1, max_size=60),
    fields=st.lists(field_st, max_size=20),
    source_pages=st.lists(st.integers(min_value=1, max_value=500), max_size=10),
)

procedure_step_st = st.builds(
    ProcedureStep,
    step_number=st.integers(min_value=1, max_value=100),
    condition=st.text(max_size=80),
    action=st.text(min_size=1, max_size=80),
)

procedure_st = st.builds(
    ProcedureRule,
    name=st.text(min_size=1, max_size=60),
    steps=st.lists(procedure_step_st, max_size=10),
    source_pages=st.lists(st.integers(min_value=1, max_value=500), max_size=10),
)

timer_st = st.builds(
    TimerConfig,
    timer_name=st.text(min_size=1, max_size=60),
    timeout_value=st.text(max_size=40),
    trigger_action=st.text(max_size=80),
    description=st.text(max_size=80),
    source_pages=st.lists(st.integers(min_value=1, max_value=500), max_size=10),
)

error_st = st.builds(
    ErrorRule,
    error_condition=st.text(min_size=1, max_size=80),
    handling_action=st.text(max_size=80),
    description=st.text(max_size=80),
    source_pages=st.lists(st.integers(min_value=1, max_value=500), max_size=10),
)

state_context_st = st.sampled_from(
    [
        build_tcp_connection_state_context(),
        build_generic_session_state_context(),
    ]
)

schema_st = st.builds(
    ProtocolSchema,
    protocol_name=st.text(min_size=1, max_size=60),
    state_machines=st.lists(state_machine_st, max_size=4),
    state_contexts=st.lists(state_context_st, max_size=4),
    messages=st.lists(message_st, max_size=4),
    procedures=st.lists(procedure_st, max_size=4),
    timers=st.lists(timer_st, max_size=4),
    errors=st.lists(error_st, max_size=4),
    constants=st.dictionaries(
        st.text(min_size=1, max_size=20),
        st.one_of(st.integers(), st.text(max_size=40), st.booleans()),
        max_size=8,
    ),
    source_document=st.text(max_size=80),
)

node_label_st = st.builds(
    NodeSemanticLabel,
    node_id=st.text(min_size=1, max_size=20),
    label=st.sampled_from(
        [
            "state_machine",
            "message_format",
            "procedure_rule",
            "timer_rule",
            "error_handling",
            "general_description",
        ]
    ),
    confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    rationale=st.text(min_size=1, max_size=100),
    secondary_hints=st.lists(
        st.sampled_from(
            [
                "state_machine",
                "message_format",
                "procedure_rule",
                "timer_rule",
                "error_handling",
                "general_description",
            ]
        ),
        max_size=5,
        unique=True,
    ),
)

label_meta_st = st.builds(
    NodeLabelMeta,
    source_document=st.text(min_size=1, max_size=80),
    model_name=st.text(min_size=1, max_size=40),
    prompt_version=st.text(min_size=1, max_size=20),
    label_priority=st.permutations(
        [
            "state_machine",
            "message_format",
            "procedure_rule",
            "timer_rule",
            "error_handling",
            "general_description",
        ]
    ).map(list),
    created_at=st.text(min_size=1, max_size=40),
)


# Feature: protocol-extraction-pipeline, Property 10: ProtocolSchema 序列化 Round-Trip
@given(schema=schema_st)
@settings(max_examples=100)
def test_protocol_schema_roundtrip(schema: ProtocolSchema):
    restored = ProtocolSchema.model_validate_json(schema.model_dump_json())
    assert restored == schema


# Feature: protocol-extraction-pipeline, Property 4: 分类数据序列化 Round-Trip
@given(labels=st.dictionaries(st.text(min_size=1, max_size=20), node_label_st, max_size=10))
@settings(max_examples=100)
def test_node_labels_roundtrip(labels: dict[str, NodeSemanticLabel]):
    payload = json.dumps(
        {node_id: label.model_dump() for node_id, label in labels.items()},
        ensure_ascii=False,
    )
    restored_raw = json.loads(payload)
    restored = {
        node_id: NodeSemanticLabel.model_validate(raw)
        for node_id, raw in restored_raw.items()
    }
    assert restored == labels


@given(meta=label_meta_st)
@settings(max_examples=100)
def test_node_label_meta_roundtrip(meta: NodeLabelMeta):
    restored = NodeLabelMeta.model_validate_json(meta.model_dump_json())
    assert restored == meta
