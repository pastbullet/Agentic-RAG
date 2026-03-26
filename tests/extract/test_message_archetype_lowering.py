"""Tests for archetype-guided lowering into unified MessageIR."""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from src.extract.codegen import generate_code
from src.extract.message_archetype import build_message_archetype_contributions
from src.extract.message_archetype_lowering import lower_archetype_contributions_to_message_irs
from src.extract.verify import _is_gcc_available, verify_generated_code
from src.models import NormalizationStatus, ProtocolField, ProtocolMessage, ProtocolSchema


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


def _compile_and_run_harness(
    generated_dir: Path,
    source_names: list[str],
    harness_name: str,
    harness_source: str,
) -> None:
    harness_path = generated_dir / harness_name
    harness_path.write_text(harness_source, encoding="utf-8")
    binary_path = generated_dir / harness_path.stem
    command = [
        "gcc",
        "-Wall",
        "-I",
        str(generated_dir),
        *[str(generated_dir / name) for name in source_names],
        str(harness_path),
        "-o",
        str(binary_path),
    ]
    compiled = subprocess.run(command, capture_output=True, text=True, check=False)
    assert compiled.returncode == 0, compiled.stderr or compiled.stdout
    executed = subprocess.run([str(binary_path)], capture_output=True, text=True, check=False)
    assert executed.returncode == 0, executed.stderr or executed.stdout


def test_tcp_archetype_lowers_to_degraded_ready_message_ir():
    contributions = build_message_archetype_contributions("rfc793-TCP", [_tcp_message()])

    message_irs = lower_archetype_contributions_to_message_irs("rfc793-TCP", contributions)

    assert len(message_irs) == 1
    message_ir = message_irs[0]
    assert message_ir.canonical_name == "tcp_header"
    assert message_ir.normalization_status == NormalizationStatus.DEGRADED_READY
    assert message_ir.layout_kind == "composite"
    assert message_ir.min_size_bits == 160
    assert message_ir.max_size_bits == 480
    assert [field.canonical_name for field in message_ir.fields] == [
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
    assert "options" not in message_ir.normalized_field_order
    assert "padding" not in message_ir.normalized_field_order
    assert len(message_ir.composite_tails) == 1
    tail = message_ir.composite_tails[0]
    assert tail.tail_kind == "option_list"
    assert tail.option_list_id == "tcp_header.options"
    assert tail.span_expression == "header.data_offset * 4 - 20"
    assert tail.max_span_bytes == 40
    assert len(message_ir.option_lists) == 1
    assert message_ir.option_lists[0].list_id == "tcp_header.options"
    assert any(diag.code == "option_list_fallback_enabled" for diag in message_ir.diagnostics)


def test_tcp_generate_code_accepts_degraded_ready_header(tmp_path: Path):
    schema = ProtocolSchema(
        protocol_name="rfc793-TCP",
        source_document="rfc793-TCP.pdf",
        state_machines=[],
        messages=[_tcp_message()],
    )

    result = generate_code(schema, str(tmp_path))

    assert [item.canonical_name for item in result.generated_message_irs] == ["tcp_header"]
    assert result.generated_message_irs[0].normalization_status == NormalizationStatus.DEGRADED_READY
    assert (tmp_path / "tcp_msg_tcp_header.h").exists()
    assert (tmp_path / "tcp_msg_tcp_header.c").exists()
    source = (tmp_path / "tcp_msg_tcp_header.c").read_text(encoding="utf-8")
    assert "options_tail_validate" in source
    assert "msg->data_offset * 4 - 20" in source


def test_tcp_verify_generated_code_roundtrips_degraded_ready_header(tmp_path: Path):
    schema = ProtocolSchema(
        protocol_name="rfc793-TCP",
        source_document="rfc793-TCP.pdf",
        state_machines=[],
        messages=[_tcp_message()],
    )
    generated_dir = tmp_path / "generated"
    result = generate_code(schema, str(generated_dir))

    report = verify_generated_code(
        str(generated_dir),
        schema,
        "rfc793-TCP.pdf",
        result.expected_symbols,
        result.generated_msg_headers,
        result.generated_msgs,
        result.generated_message_irs,
    )

    assert report.syntax_checked is True
    assert report.syntax_ok is True
    assert report.syntax_errors == []
    assert report.test_results == [
        {"test_name": "test_roundtrip_stub", "passed": True, "error": ""},
        {"test_name": "test_roundtrip_runtime", "passed": True, "error": ""},
    ]


@pytest.mark.skipif(not _is_gcc_available(), reason="gcc not available")
def test_tcp_generated_header_roundtrips_without_options(tmp_path: Path):
    schema = ProtocolSchema(
        protocol_name="rfc793-TCP",
        source_document="rfc793-TCP.pdf",
        state_machines=[],
        messages=[_tcp_message()],
    )
    generated_dir = tmp_path / "generated"
    generate_code(schema, str(generated_dir))

    _compile_and_run_harness(
        generated_dir,
        ["tcp_msg_tcp_header.c"],
        "test_tcp_header_roundtrip.c",
        r'''
#include <stdint.h>
#include "tcp_msg_tcp_header.h"

int main(void) {
    tcp_tcp_header input = {0};
    tcp_tcp_header decoded = {0};
    uint8_t buffer[64] = {0};
    int rc = 0;

    input.source_port = 80;
    input.destination_port = 443;
    input.sequence_number = 0x01020304u;
    input.acknowledgment_number = 0x05060708u;
    input.data_offset = 5;
    input.reserved = 0;
    input.ack = 1;
    input.syn = 1;
    input.window = 4096;
    input.checksum = 0x1234;
    input.urgent_pointer = 0;
    input.options_tail.item_count = 0;
    input.options_tail.encoded_len = 0;
    input.options_tail.opaque_remainder_len = 0;

    rc = tcp_tcp_header_validate(&input);
    if (rc != 0) return 1;
    rc = tcp_tcp_header_pack(&input, buffer, sizeof(buffer));
    if (rc != 20) return 1;
    rc = tcp_tcp_header_unpack(&decoded, buffer, (size_t)rc);
    if (rc != 20) return 1;
    if (decoded.data_offset != input.data_offset) return 1;
    if (decoded.options_tail.encoded_len != 0) return 1;
    if (decoded.options_tail.item_count != 0) return 1;
    return 0;
}
''',
    )


@pytest.mark.skipif(not _is_gcc_available(), reason="gcc not available")
def test_tcp_generated_header_validate_rejects_tail_length_mismatch(tmp_path: Path):
    schema = ProtocolSchema(
        protocol_name="rfc793-TCP",
        source_document="rfc793-TCP.pdf",
        state_machines=[],
        messages=[_tcp_message()],
    )
    generated_dir = tmp_path / "generated"
    generate_code(schema, str(generated_dir))

    _compile_and_run_harness(
        generated_dir,
        ["tcp_msg_tcp_header.c"],
        "test_tcp_header_invalid_tail.c",
        r'''
#include "tcp_msg_tcp_header.h"

int main(void) {
    tcp_tcp_header msg = {0};
    msg.data_offset = 5;
    msg.reserved = 0;
    msg.options_tail.encoded_len = 4;
    if (tcp_tcp_header_validate(&msg) == 0) return 1;
    return 0;
}
''',
    )
