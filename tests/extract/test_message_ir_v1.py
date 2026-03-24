"""Focused integration tests for MessageIR v1 lowering, normalization, and codegen."""

from __future__ import annotations

from pathlib import Path

from src.extract.codegen import generate_code
from src.extract.message_ir import build_message_ir_registry, lower_protocol_messages_to_message_ir
from src.extract.verify import verify_generated_code
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


def test_lowering_normalizes_bfd_auth_sections_and_blocks_control_packet():
    schema = _load_bfd_schema()

    message_irs = lower_protocol_messages_to_message_ir(schema.protocol_name, schema.messages)
    by_name = {item.canonical_name: item for item in message_irs}

    assert set(by_name) == {
        "bfd_auth_keyed_md5",
        "bfd_auth_keyed_sha1",
        "bfd_auth_simple_password",
        "generic_bfd_control_packet",
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

    control = by_name["generic_bfd_control_packet"]
    assert control.normalization_status == NormalizationStatus.BLOCKED
    assert any(item.code == "message_scope_deferred" for item in control.diagnostics)


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


def test_codegen_only_emits_ready_message_irs_for_bfd_auth_sections(tmp_path: Path):
    schema = _auth_only_schema()

    result = generate_code(schema, str(tmp_path))

    assert len(result.generated_message_irs) == 3
    assert all(item.normalization_status == NormalizationStatus.READY for item in result.generated_message_irs)
    assert len(result.generated_msg_headers) == 3
    assert any("Generic BFD Control Packet" in warning for warning in result.warnings)
    assert any(symbol["symbol"].endswith("_validate") for symbol in result.expected_symbols)

    simple_source = tmp_path / "bfd_msg_bfd_auth_simple_password_authentication.c"
    md5_source = tmp_path / "bfd_msg_keyed_md5_and_meticulous_keyed_md5_auth.c"
    assert "password_len == (size_t)(msg->auth_len - 3)" in simple_source.read_text(encoding="utf-8")
    assert "msg->reserved == 0" in md5_source.read_text(encoding="utf-8")


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
