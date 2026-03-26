"""Tests for generated TCP option-list code paths."""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from src.extract.codegen import generate_code
from src.extract.verify import _is_gcc_available, verify_generated_code
from src.models import ProtocolField, ProtocolMessage, ProtocolSchema


def _tcp_schema() -> ProtocolSchema:
    return ProtocolSchema(
        protocol_name="rfc793-TCP",
        source_document="rfc793-TCP.pdf",
        state_machines=[],
        messages=[
            ProtocolMessage(
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


@pytest.mark.skipif(not _is_gcc_available(), reason="gcc not available")
def test_tcp_generated_header_roundtrips_supported_option_set(tmp_path: Path):
    generated_dir = tmp_path / "generated"
    schema = _tcp_schema()
    result = generate_code(schema, str(generated_dir))
    verify_generated_code(
        str(generated_dir),
        schema,
        schema.source_document or "rfc793-TCP.pdf",
        result.expected_symbols,
        result.generated_msg_headers,
        result.generated_msgs,
        result.generated_message_irs,
    )

    roundtrip_source = (generated_dir / "test_roundtrip.c").read_text(encoding="utf-8")
    assert "items[index].kind" in roundtrip_source
    assert "mss_value" in roundtrip_source
    assert "shift_count" in roundtrip_source

    _compile_and_run_harness(
        generated_dir,
        ["tcp_msg_tcp_header.c"],
        "test_tcp_options_roundtrip.c",
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
    input.data_offset = 8;
    input.reserved = 0;
    input.ack = 1;
    input.syn = 1;
    input.window = 4096;
    input.checksum = 0x1234;
    input.urgent_pointer = 0;

    input.options_tail.item_count = 4;
    input.options_tail.items[0].kind = tcp_tcp_header_options_tail_item_kind_MSS;
    input.options_tail.items[0].mss_value = 1460;
    input.options_tail.items[1].kind = tcp_tcp_header_options_tail_item_kind_NOP;
    input.options_tail.items[2].kind = tcp_tcp_header_options_tail_item_kind_WINDOW_SCALE;
    input.options_tail.items[2].shift_count = 7;
    input.options_tail.items[3].kind = tcp_tcp_header_options_tail_item_kind_EOL;
    input.options_tail.opaque_remainder_len = 3;
    input.options_tail.opaque_remainder[0] = 0;
    input.options_tail.opaque_remainder[1] = 0;
    input.options_tail.opaque_remainder[2] = 0;
    input.options_tail.encoded_len = 12;

    rc = tcp_tcp_header_validate(&input);
    if (rc != 0) return 1;
    rc = tcp_tcp_header_pack(&input, buffer, sizeof(buffer));
    if (rc != 32) return 1;
    if (buffer[20] != 2 || buffer[21] != 4 || buffer[22] != 0x05 || buffer[23] != 0xb4) return 1;
    if (buffer[24] != 1) return 1;
    if (buffer[25] != 3 || buffer[26] != 3 || buffer[27] != 7) return 1;
    if (buffer[28] != 0 || buffer[29] != 0 || buffer[30] != 0 || buffer[31] != 0) return 1;

    rc = tcp_tcp_header_unpack(&decoded, buffer, (size_t)rc);
    if (rc != 32) return 1;
    if (decoded.options_tail.item_count != 4) return 1;
    if (decoded.options_tail.items[0].kind != tcp_tcp_header_options_tail_item_kind_MSS) return 1;
    if (decoded.options_tail.items[0].mss_value != 1460) return 1;
    if (decoded.options_tail.items[1].kind != tcp_tcp_header_options_tail_item_kind_NOP) return 1;
    if (decoded.options_tail.items[2].kind != tcp_tcp_header_options_tail_item_kind_WINDOW_SCALE) return 1;
    if (decoded.options_tail.items[2].shift_count != 7) return 1;
    if (decoded.options_tail.items[3].kind != tcp_tcp_header_options_tail_item_kind_EOL) return 1;
    if (decoded.options_tail.opaque_remainder_len != 3) return 1;
    return 0;
}
''',
    )


@pytest.mark.skipif(not _is_gcc_available(), reason="gcc not available")
def test_tcp_generated_header_unpack_rejects_invalid_option_length(tmp_path: Path):
    generated_dir = tmp_path / "generated"
    generate_code(_tcp_schema(), str(generated_dir))

    _compile_and_run_harness(
        generated_dir,
        ["tcp_msg_tcp_header.c"],
        "test_tcp_options_invalid_length.c",
        r'''
#include <stdint.h>
#include "tcp_msg_tcp_header.h"

int main(void) {
    tcp_tcp_header decoded = {0};
    uint8_t buffer[24] = {0};
    int rc = 0;

    buffer[12] = 0x50; /* data_offset=6, reserved=0 */
    buffer[13] = 0x00;
    buffer[20] = 2;
    buffer[21] = 3;
    buffer[22] = 0x05;
    buffer[23] = 0xb4;

    rc = tcp_tcp_header_unpack(&decoded, buffer, sizeof(buffer));
    if (rc == 0) return 1;
    return 0;
}
''',
    )
