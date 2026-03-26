"""Tests for archetype-guided message extraction sidecars."""

from __future__ import annotations

from src.extract.message_archetype import (
    build_message_archetype_contribution_from_message,
    build_message_archetype_contributions,
)
from src.extract.merge import ExtractionRecord
from src.extract.message_archetype_models import CompositionTrait, CoreArchetype, TailKind
from src.models import ProtocolField, ProtocolMessage


def _tcp_message() -> ProtocolMessage:
    return ProtocolMessage(
        name="TCP Header",
        source_pages=[21, 22, 23, 24],
        fields=[
            ProtocolField(name="Source Port", size_bits=16, description="The source port number."),
            ProtocolField(name="Destination Port", size_bits=16, description="The destination port number."),
            ProtocolField(name="Sequence Number", size_bits=32, description="Sequence number."),
            ProtocolField(name="Acknowledgment Number", size_bits=32, description="Acknowledgment number."),
            ProtocolField(name="Data Offset", size_bits=4, description="Header size in 32-bit words."),
            ProtocolField(name="Reserved", size_bits=6, description="Reserved. Must be zero."),
            ProtocolField(name="URG", size_bits=1, description="Urgent."),
            ProtocolField(name="ACK", size_bits=1, description="Ack."),
            ProtocolField(name="PSH", size_bits=1, description="Push."),
            ProtocolField(name="RST", size_bits=1, description="Reset."),
            ProtocolField(name="SYN", size_bits=1, description="Sync."),
            ProtocolField(name="FIN", size_bits=1, description="Finish."),
            ProtocolField(name="Window", size_bits=16, description="Window."),
            ProtocolField(name="Checksum", size_bits=16, description="Checksum."),
            ProtocolField(name="Urgent Pointer", size_bits=16, description="Urgent pointer."),
            ProtocolField(name="Options", size_bits=None, description="Variable-length options tail."),
            ProtocolField(name="Padding", size_bits=None, description="Derived zero padding."),
        ],
    )


def test_build_tcp_header_archetype_contribution():
    message = _tcp_message()
    record = ExtractionRecord(
        node_id="0021",
        title="3.1 Header Format",
        label="message_format",
        confidence=0.97,
        source_pages=[21, 22, 23, 24],
        payload=message.model_dump(),
    )

    contributions = build_message_archetype_contributions(
        "rfc793-TCP",
        [message],
        extraction_records=[record],
    )

    assert len(contributions) == 1
    contribution = contributions[0]
    assert contribution.message_name == "TCP Header"
    assert contribution.canonical_hint == "tcp_header"
    assert contribution.core_archetype == CoreArchetype.PACKED_HEADER
    assert contribution.composition_traits == [
        CompositionTrait.HEADER_LENGTH_CONTROLLED_TAIL,
        CompositionTrait.DERIVED_PADDING,
    ]
    assert contribution.source_pages == [21, 22, 23, 24]
    assert contribution.source_node_ids == ["0021"]
    assert [field.canonical_hint for field in contribution.fields] == [
        "source_port",
        "destination_port",
        "sequence_number",
        "acknowledgment_number",
        "data_offset",
        "reserved",
        "urg",
        "ack",
        "psh",
        "rst",
        "syn",
        "fin",
        "window",
        "checksum",
        "urgent_pointer",
    ]

    tail = contribution.tail_slots[0]
    assert tail.slot_name == "options_tail"
    assert tail.presence_expression == "header.data_offset > 5"
    assert tail.span_expression == "header.data_offset * 4 - 20"
    assert tail.tail_kind == TailKind.OPAQUE_BYTES
    assert tail.max_span_bytes == 40
    assert contribution.diagnostics == []


def test_native_sidecar_is_preferred_over_reconstruction():
    message = _tcp_message()
    sidecar = build_message_archetype_contribution_from_message(message, source_node_ids=["native-node"])
    assert sidecar is not None

    message_with_sidecar = message.model_copy(update={"archetype_contribution": sidecar.model_dump()}, deep=True)
    contributions = build_message_archetype_contributions("rfc793-TCP", [message_with_sidecar])

    assert len(contributions) == 1
    contribution = contributions[0]
    assert contribution.source_node_ids == ["native-node"]
