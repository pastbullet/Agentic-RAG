"""Tests for lowering TCP options tail into a structured option list."""

from __future__ import annotations

from src.extract.message_archetype import build_message_archetype_contributions
from src.extract.message_archetype_lowering import lower_archetype_contributions_to_message_irs
from src.extract.message_ir import normalize_message_ir
from src.models import NormalizationStatus, ProtocolField, ProtocolMessage


def _tcp_message() -> ProtocolMessage:
    return ProtocolMessage(
        name="TCP Header",
        source_pages=[21, 22, 23, 24],
        fields=[
            ProtocolField(name="Source Port", size_bits=16),
            ProtocolField(name="Destination Port", size_bits=16),
            ProtocolField(name="Sequence Number", size_bits=32),
            ProtocolField(name="Acknowledgment Number", size_bits=32),
            ProtocolField(name="Data Offset", size_bits=4),
            ProtocolField(name="Reserved", size_bits=6),
            ProtocolField(name="URG", size_bits=1),
            ProtocolField(name="ACK", size_bits=1),
            ProtocolField(name="PSH", size_bits=1),
            ProtocolField(name="RST", size_bits=1),
            ProtocolField(name="SYN", size_bits=1),
            ProtocolField(name="FIN", size_bits=1),
            ProtocolField(name="Window", size_bits=16),
            ProtocolField(name="Checksum", size_bits=16),
            ProtocolField(name="Urgent Pointer", size_bits=16),
            ProtocolField(name="Options", size_bits=None),
            ProtocolField(name="Padding", size_bits=None),
        ],
    )


def test_tcp_option_tail_lowers_to_option_list_ir():
    contribution = build_message_archetype_contributions("rfc793-TCP", [_tcp_message()])[0]

    message_ir = lower_archetype_contributions_to_message_irs("rfc793-TCP", [contribution])[0]

    assert message_ir.canonical_name == "tcp_header"
    assert message_ir.normalization_status == NormalizationStatus.READY
    assert len(message_ir.option_lists) == 1
    option_list = message_ir.option_lists[0]
    assert option_list.parent_message_ir_id == "tcp_header"
    assert option_list.parent_section_id == "tcp_header.options_tail"
    assert option_list.span_expression == "header.data_offset * 4 - 20"
    assert option_list.max_size_bytes == 40
    assert [item.kind_name for item in option_list.items] == ["eol", "nop", "mss", "window_scale"]
    assert option_list.fallback_mode == "opaque_remainder"
    assert option_list.fallback_triggered is False
    tail = message_ir.composite_tails[0]
    assert tail.tail_kind == "option_list"
    assert tail.option_list_id == option_list.list_id
    section = next(section for section in message_ir.sections if section.section_id == tail.section_id)
    assert section.option_list_id == option_list.list_id
    assert any(diag.code == "option_list_fallback_enabled" for diag in message_ir.diagnostics)


def test_tcp_option_tail_keeps_span_controlled_by_data_offset():
    contribution = build_message_archetype_contributions("rfc793-TCP", [_tcp_message()])[0]

    message_ir = lower_archetype_contributions_to_message_irs("rfc793-TCP", [contribution])[0]

    tail = message_ir.composite_tails[0]
    assert tail.presence_rule_id == "tcp_header.options_tail.presence"
    assert tail.span_expression == "header.data_offset * 4 - 20"
    assert tail.fixed_prefix_bits == 160
    assert tail.max_span_bytes == 40


def test_tcp_option_tail_can_remain_degraded_when_fallback_is_triggered():
    contribution = build_message_archetype_contributions("rfc793-TCP", [_tcp_message()])[0]

    message_ir = lower_archetype_contributions_to_message_irs("rfc793-TCP", [contribution])[0]
    message_ir.option_lists[0].fallback_triggered = True

    renormalized = normalize_message_ir(message_ir)

    assert renormalized.normalization_status == NormalizationStatus.DEGRADED_READY
