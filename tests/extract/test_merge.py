"""Unit and property tests for Phase 1 merge helpers."""

from __future__ import annotations

import json
from dataclasses import asdict

from hypothesis import given, settings
from hypothesis import strategies as st

from src.extract.merge import (
    ExtractionRecord,
    build_merge_report,
    is_empty_error,
    is_empty_message,
    is_empty_procedure,
    is_empty_state_machine,
    is_empty_timer,
    merge_messages,
    merge_timers,
    normalize_name,
)
from src.models import (
    ErrorRule,
    ProcedureRule,
    ProtocolField,
    ProtocolMessage,
    ProtocolState,
    ProtocolStateMachine,
    TimerConfig,
)


def test_normalize_name_removes_section_prefixes_and_rfc_references():
    assert normalize_name("6.8.1 BFD State Machine") == "bfd state machine"
    assert normalize_name("RFC 5880 Detection Time") == "detection time"
    assert normalize_name("§6.8.5–6.8.6 Detection Time") == "detection time"
    assert normalize_name("(RFC 5880 §6.2) Detection Time") == "detection time"


def test_normalize_name_preserves_semantic_words_and_handles_empty_input():
    assert normalize_name("state machine") == "state machine"
    assert normalize_name("procedure") == "procedure"
    assert normalize_name("overview") == "overview"
    assert normalize_name("") == ""
    assert normalize_name(None) == ""


def test_empty_predicates_distinguish_empty_and_non_empty_objects():
    assert is_empty_state_machine(ProtocolStateMachine(name="sm")) is True
    assert (
        is_empty_state_machine(
            ProtocolStateMachine(name="sm", states=[ProtocolState(name="Down")])
        )
        is False
    )

    assert is_empty_message(ProtocolMessage(name="msg")) is True
    assert is_empty_message(
        ProtocolMessage(name="msg", fields=[ProtocolField(name="Version")])
    ) is False

    assert is_empty_procedure(ProcedureRule(name="proc")) is True
    assert is_empty_procedure(
        ProcedureRule(name="proc", steps=[{"step_number": 1, "action": "Send"}])
    ) is False

    assert is_empty_timer(TimerConfig(timer_name="Detection Time")) is True
    assert is_empty_timer(
        TimerConfig(timer_name="Detection Time", description="declare session down")
    ) is False

    assert is_empty_error(ErrorRule(error_condition="bad", handling_action="")) is True
    assert is_empty_error(
        ErrorRule(error_condition="bad", handling_action="", description="discard packet")
    ) is False


def test_merge_timers_keeps_single_timer_unchanged():
    timer = TimerConfig(
        timer_name="Detection Time",
        timeout_value="3 x interval",
        trigger_action="declare session down",
        description="base timer",
        source_pages=[12],
    )

    merged, groups = merge_timers([timer])

    assert merged == [timer]
    assert groups == []


def test_merge_timers_combines_same_named_variants():
    timers = [
        TimerConfig(
            timer_name="Detection Time",
            timeout_value="3 x interval",
            trigger_action="down",
            description="short",
            source_pages=[12],
        ),
        TimerConfig(
            timer_name="6.8.4 Detection Time",
            timeout_value="Detect Mult * negotiated receive interval",
            trigger_action="declare session down immediately",
            description="longer detection time description",
            source_pages=[13, 15],
        ),
        TimerConfig(
            timer_name="Idle Hold Timer",
            timeout_value="1 second",
            source_pages=[20],
        ),
    ]

    merged, groups = merge_timers(timers)

    assert len(merged) == 2
    detection = next(item for item in merged if normalize_name(item.timer_name) == "detection time")
    assert detection.source_pages == [12, 13, 15]
    assert detection.description == "longer detection time description"
    assert detection.trigger_action == "declare session down immediately"
    assert detection.timeout_value == "Detect Mult * negotiated receive interval"
    assert groups == [
        {
            "normalized_key": "detection time",
            "merged_from": ["Detection Time", "6.8.4 Detection Time"],
            "source_pages_union": [12, 13, 15],
            "timeout_value_variants": [
                "3 x interval",
                "Detect Mult * negotiated receive interval",
            ],
        }
    ]


def test_merge_messages_keeps_single_message_unchanged():
    message = ProtocolMessage(
        name="BFD Control Packet",
        fields=[ProtocolField(name="Version", size_bits=3)],
        source_pages=[5],
    )

    merged, groups = merge_messages([message])

    assert merged == [message]
    assert groups == []


def test_merge_messages_combines_same_named_messages_and_deduplicates_fields():
    messages = [
        ProtocolMessage(
            name="BFD Control Packet",
            fields=[
                ProtocolField(name="Version", size_bits=None, type="", description="short"),
                ProtocolField(name="Length", size_bits=8, type="uint8", description="payload length"),
            ],
            source_pages=[7],
        ),
        ProtocolMessage(
            name="6.5 BFD Control Packet",
            fields=[
                ProtocolField(name="version", size_bits=3, type="uint3", description="protocol version"),
                ProtocolField(name="Length", size_bits=None, type="", description="len"),
                ProtocolField(name="Detect Mult", size_bits=8, type="uint8", description="detect multiplier"),
            ],
            source_pages=[8, 9],
        ),
    ]

    merged, groups = merge_messages(messages)

    assert len(merged) == 1
    merged_message = merged[0]
    assert merged_message.name == "6.5 BFD Control Packet"
    assert merged_message.source_pages == [7, 8, 9]
    assert [field.name for field in merged_message.fields] == ["Version", "Length", "Detect Mult"]
    version = merged_message.fields[0]
    assert version.size_bits == 3
    assert version.description == "protocol version"
    assert version.type == "uint3"
    length = merged_message.fields[1]
    assert length.size_bits == 8
    assert length.description == "payload length"
    assert length.type == "uint8"
    assert groups == [
        {
            "normalized_key": "bfd control packet",
            "merged_from": ["BFD Control Packet", "6.5 BFD Control Packet"],
            "source_pages_union": [7, 8, 9],
            "field_count_before": 5,
            "field_count_after": 3,
        }
    ]


def test_build_merge_report_contains_expected_sections():
    report = build_merge_report(
        pre={"timer": 3, "message": 2},
        dropped={"timer": 1, "message": 0},
        post_filter={"timer": 2, "message": 2},
        post={"timer": 1, "message": 1},
        timer_groups=[{"normalized_key": "detection time"}],
        message_groups=[{"normalized_key": "bfd control packet"}],
    )

    assert set(report) == {
        "pre_merge_counts",
        "dropped_empty_counts",
        "post_filter_counts",
        "post_merge_counts",
        "merged_groups",
    }
    assert set(report["merged_groups"]) == {"timer", "message"}


@given(
    timer_names=st.lists(st.sampled_from(["Detection Time", "Idle Hold Timer"]), min_size=1, max_size=6),
)
@settings(max_examples=100)
def test_merge_timers_count_is_monotonic(timer_names: list[str]):
    timers = [
        TimerConfig(timer_name=name, timeout_value=f"v-{idx}", source_pages=[idx + 1])
        for idx, name in enumerate(timer_names)
    ]

    merged, _ = merge_timers(timers)

    assert len(merged) <= len(timers)


@given(
    source_page_lists=st.lists(
        st.lists(st.integers(min_value=1, max_value=20), min_size=1, max_size=4, unique=True),
        min_size=2,
        max_size=5,
    )
)
@settings(max_examples=100)
def test_merge_timers_source_pages_are_a_union(source_page_lists: list[list[int]]):
    timers = [
        TimerConfig(timer_name="Detection Time", timeout_value=f"v-{idx}", source_pages=pages)
        for idx, pages in enumerate(source_page_lists)
    ]

    merged, _ = merge_timers(timers)

    assert len(merged) == 1
    expected = sorted({page for pages in source_page_lists for page in pages})
    assert merged[0].source_pages == expected


@given(
    names=st.lists(st.sampled_from(["BFD Control Packet", "Echo Packet"]), min_size=1, max_size=6),
)
@settings(max_examples=100)
def test_merge_messages_count_is_monotonic(names: list[str]):
    messages = [
        ProtocolMessage(
            name=name,
            fields=[ProtocolField(name=f"Field {idx}", size_bits=8)],
            source_pages=[idx + 1],
        )
        for idx, name in enumerate(names)
    ]

    merged, _ = merge_messages(messages)

    assert len(merged) <= len(messages)


@given(
    page_lists=st.lists(
        st.lists(st.integers(min_value=1, max_value=20), min_size=1, max_size=4, unique=True),
        min_size=2,
        max_size=5,
    )
)
@settings(max_examples=100)
def test_merge_messages_source_pages_are_a_union(page_lists: list[list[int]]):
    messages = [
        ProtocolMessage(
            name="BFD Control Packet",
            fields=[ProtocolField(name=f"Field {idx}", size_bits=8)],
            source_pages=pages,
        )
        for idx, pages in enumerate(page_lists)
    ]

    merged, _ = merge_messages(messages)

    assert len(merged) == 1
    expected = sorted({page for pages in page_lists for page in pages})
    assert merged[0].source_pages == expected


@given(
    records=st.lists(
        st.builds(
            ExtractionRecord,
            node_id=st.text(min_size=1, max_size=8),
            title=st.text(min_size=0, max_size=20),
            label=st.sampled_from(
                ["state_machine", "message_format", "procedure_rule", "timer_rule", "error_handling"]
            ),
            confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
            source_pages=st.lists(st.integers(min_value=1, max_value=20), max_size=4),
            payload=st.dictionaries(
                st.text(min_size=1, max_size=8),
                st.one_of(
                    st.text(max_size=20),
                    st.integers(min_value=0, max_value=20),
                    st.lists(st.integers(min_value=0, max_value=10), max_size=3),
                ),
                max_size=4,
            ),
        ),
        max_size=10,
    )
)
@settings(max_examples=100)
def test_extraction_record_json_round_trip(records: list[ExtractionRecord]):
    payload = [asdict(record) for record in records]
    encoded = json.dumps(payload, ensure_ascii=False)
    decoded = json.loads(encoded)
    restored = [ExtractionRecord(**item) for item in decoded]

    assert restored == records
