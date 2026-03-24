"""Focused integration tests for MessageIR v1 lowering, normalization, and codegen."""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from src.extract.codegen import generate_code
from src.extract.message_ir import build_message_ir_registry, lower_protocol_messages_to_message_ir
from src.extract.verify import _is_gcc_available, verify_generated_code
from src.models import NormalizationStatus, ProtocolField, ProtocolMessage, ProtocolSchema


def _load_bfd_schema() -> ProtocolSchema:
    return ProtocolSchema.model_validate_json(
        Path("data/out/rfc5880-BFD/protocol_schema.json").read_text(encoding="utf-8")
    )


def _auth_only_schema() -> ProtocolSchema:
    full = _load_bfd_schema()
    schema = full.model_copy(deep=True)
    schema.state_machines = []
    return schema


def _generate_auth_codegen(tmp_path: Path):
    schema = _auth_only_schema()
    generated_dir = tmp_path / "generated"
    result = generate_code(schema, str(generated_dir))
    return schema, generated_dir, result


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
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr or completed.stdout
    executed = subprocess.run([str(binary_path)], capture_output=True, text=True, check=False)
    assert executed.returncode == 0, executed.stderr or executed.stdout


def test_lowering_normalizes_bfd_auth_sections_and_control_packet_mandatory_section():
    schema = _load_bfd_schema()

    message_irs = lower_protocol_messages_to_message_ir(schema.protocol_name, schema.messages)
    by_name = {item.canonical_name: item for item in message_irs}

    assert set(by_name) == {
        "bfd_auth_keyed_md5",
        "bfd_auth_keyed_sha1",
        "bfd_auth_simple_password",
        "bfd_control_packet",
        "bfd_control_packet_mandatory",
    }

    simple = by_name["bfd_auth_simple_password"]
    assert simple.normalization_status == NormalizationStatus.READY
    assert simple.layout_kind == "variable_length"
    assert simple.normalized_field_order == ["auth_type", "auth_len", "auth_key_id", "password"]
    assert simple.min_size_bits == 32
    assert simple.max_size_bits == 152
    assert [field.resolved_byte_offset for field in simple.fields] == [0, 1, 2, 3]

    md5 = by_name["bfd_auth_keyed_md5"]
    assert md5.normalization_status == NormalizationStatus.READY
    assert md5.layout_kind == "fixed_bytes"
    assert md5.total_size_bytes == 24

    sha1 = by_name["bfd_auth_keyed_sha1"]
    assert sha1.normalization_status == NormalizationStatus.READY
    assert sha1.layout_kind == "fixed_bytes"
    assert sha1.total_size_bytes == 28

    control = by_name["bfd_control_packet_mandatory"]
    assert control.normalization_status == NormalizationStatus.READY
    assert control.layout_kind == "bitfield_packed"
    assert control.total_size_bytes == 24
    assert any(item.code == "deferred_optional_fields" for item in control.diagnostics)
    assert not any(item.code == "bitfield_not_supported_v1" for item in control.diagnostics)

    full_control = by_name["bfd_control_packet"]
    assert full_control.normalization_status == NormalizationStatus.READY
    assert full_control.layout_kind == "composite"
    assert full_control.min_size_bits == 192
    assert full_control.max_size_bits == 416
    assert len(full_control.composite_tails) == 1
    assert full_control.composite_tails[0].candidate_message_irs == [
        "bfd_auth_simple_password",
        "bfd_auth_keyed_md5",
        "bfd_auth_keyed_sha1",
    ]
    assert full_control.composite_tails[0].total_length_field == "header.length"


def test_merge_conflict_records_diagnostic_and_blocks_ready():
    registry = build_message_ir_registry(
        "rfc5880-BFD",
        [
            ProtocolMessage(
                name="Conflicting Test Message",
                fields=[
                    ProtocolField(name="Field A", size_bits=8),
                    ProtocolField(name="Field B", size_bits=8),
                ],
            ),
            ProtocolMessage(
                name="Conflicting Test Message",
                fields=[
                    ProtocolField(name="Field A", size_bits=16),
                    ProtocolField(name="Field B", size_bits=8),
                ],
            ),
        ],
    )

    message_ir = registry["conflicting_test_message"]

    assert message_ir.normalization_status == NormalizationStatus.BLOCKED
    assert any(item.code == "field_width_conflict" for item in message_ir.diagnostics)


def test_codegen_only_emits_ready_message_irs_for_bfd_messages(tmp_path: Path):
    schema = _auth_only_schema()

    result = generate_code(schema, str(tmp_path))

    assert len(result.generated_message_irs) == 5
    assert all(item.normalization_status == NormalizationStatus.READY for item in result.generated_message_irs)
    assert len(result.generated_msg_headers) == 5
    assert result.warnings == []
    assert any(symbol["symbol"].endswith("_validate") for symbol in result.expected_symbols)

    simple_source = tmp_path / "bfd_msg_bfd_auth_simple_password_authentication.c"
    md5_source = tmp_path / "bfd_msg_keyed_md5_and_meticulous_keyed_md5_auth.c"
    composite_source = tmp_path / "bfd_msg_bfd_control_packet.c"
    control_source = tmp_path / "bfd_msg_bfd_control_packet_mandatory_section.c"
    assert "password_len == (size_t)(msg->auth_len - 3)" in simple_source.read_text(encoding="utf-8")
    assert "msg->reserved == 0" in md5_source.read_text(encoding="utf-8")
    assert "authentication_tail_kind" in composite_source.read_text(encoding="utf-8")
    assert "bfd_bfd_auth_simple_password_authentication_pack" in composite_source.read_text(encoding="utf-8")
    assert "packed_word_0" in control_source.read_text(encoding="utf-8")


def test_verify_generated_code_roundtrips_ready_bfd_auth_sections(tmp_path: Path):
    schema = _auth_only_schema()
    generated_dir = tmp_path / "generated"
    result = generate_code(schema, str(generated_dir))

    report = verify_generated_code(
        str(generated_dir),
        schema,
        schema.source_document or "rfc5880-BFD.pdf",
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
def test_generated_simple_password_validate_rejects_invalid_auth_type(tmp_path: Path):
    _, generated_dir, _ = _generate_auth_codegen(tmp_path)

    _compile_and_run_harness(
        generated_dir,
        ["bfd_msg_bfd_auth_simple_password_authentication.c"],
        "test_invalid_auth_type.c",
        r'''
#include "bfd_msg_bfd_auth_simple_password_authentication.h"

int main(void) {
    bfd_bfd_auth_simple_password_authentication msg = {0};
    msg.auth_type = 2;
    msg.auth_len = 4;
    msg.auth_key_id = 7;
    msg.password_len = 1;
    msg.password[0] = 0x42;
    if (bfd_bfd_auth_simple_password_authentication_validate(&msg) == 0) {
        return 1;
    }
    return 0;
}
''',
    )


@pytest.mark.skipif(not _is_gcc_available(), reason="gcc not available")
def test_generated_simple_password_validate_rejects_length_mismatch(tmp_path: Path):
    _, generated_dir, _ = _generate_auth_codegen(tmp_path)

    _compile_and_run_harness(
        generated_dir,
        ["bfd_msg_bfd_auth_simple_password_authentication.c"],
        "test_length_mismatch.c",
        r'''
#include "bfd_msg_bfd_auth_simple_password_authentication.h"

int main(void) {
    bfd_bfd_auth_simple_password_authentication msg = {0};
    msg.auth_type = 1;
    msg.auth_len = 6;
    msg.auth_key_id = 7;
    msg.password_len = 1;
    msg.password[0] = 0x24;
    if (bfd_bfd_auth_simple_password_authentication_validate(&msg) == 0) {
        return 1;
    }
    return 0;
}
''',
    )


@pytest.mark.skipif(not _is_gcc_available(), reason="gcc not available")
def test_generated_keyed_md5_validate_rejects_reserved_nonzero(tmp_path: Path):
    _, generated_dir, _ = _generate_auth_codegen(tmp_path)

    _compile_and_run_harness(
        generated_dir,
        ["bfd_msg_keyed_md5_and_meticulous_keyed_md5_auth.c"],
        "test_reserved_nonzero.c",
        r'''
#include "bfd_msg_keyed_md5_and_meticulous_keyed_md5_auth.h"

int main(void) {
    bfd_keyed_md5_and_meticulous_keyed_md5_auth msg = {0};
    msg.auth_type = 2;
    msg.auth_len = 24;
    msg.auth_key_id = 1;
    msg.reserved = 1;
    msg.sequence_number = 0x12345678u;
    if (bfd_keyed_md5_and_meticulous_keyed_md5_auth_validate(&msg) == 0) {
        return 1;
    }
    return 0;
}
''',
    )


@pytest.mark.skipif(not _is_gcc_available(), reason="gcc not available")
def test_generated_simple_password_unpack_rejects_truncated_buffer(tmp_path: Path):
    _, generated_dir, _ = _generate_auth_codegen(tmp_path)

    _compile_and_run_harness(
        generated_dir,
        ["bfd_msg_bfd_auth_simple_password_authentication.c"],
        "test_truncated_unpack.c",
        r'''
#include <stdint.h>
#include "bfd_msg_bfd_auth_simple_password_authentication.h"

int main(void) {
    const uint8_t truncated[3] = {1, 4, 9};
    bfd_bfd_auth_simple_password_authentication msg = {0};
    if (bfd_bfd_auth_simple_password_authentication_unpack(&msg, truncated, sizeof(truncated)) == 0) {
        return 1;
    }
    return 0;
}
''',
    )
