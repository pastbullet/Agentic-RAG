"""Phase 2 tests for packed bitfield layout support."""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from src.extract.codegen import generate_code
from src.extract.message_ir import build_packed_containers, lower_protocol_messages_to_message_ir, normalize_message_ir
from src.extract.verify import _is_gcc_available
from src.models import (
    CompositeDispatchCaseIR,
    CompositeTailIR,
    FieldIR,
    MessageIR,
    NormalizationStatus,
    PresenceRule,
    ProtocolSchema,
    SectionIR,
)


def _auth_only_schema() -> ProtocolSchema:
    schema = ProtocolSchema.model_validate_json(
        Path("data/out/rfc5880-BFD/protocol_schema.json").read_text(encoding="utf-8")
    )
    schema = schema.model_copy(deep=True)
    schema.state_machines = []
    return schema


def _composite_source_names() -> list[str]:
    return [
        "bfd_msg_bfd_control_packet.c",
        "bfd_msg_bfd_auth_simple_password_authentication.c",
        "bfd_msg_keyed_md5_and_meticulous_keyed_md5_auth.c",
        "bfd_msg_bfd_auth_keyed_sha1_meticulous_keyed_sha1.c",
    ]


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


def test_normalization_accepts_generic_packed_header_fixture():
    message_ir = MessageIR(
        ir_id="generic_packed_header",
        protocol_name="fixture",
        canonical_name="generic_packed_header",
        display_name="Generic Packed Header",
        fields=[
            FieldIR(field_id="generic.flag_a", name="Flag A", canonical_name="flag_a", declared_bit_width=3, declared_bit_offset=0),
            FieldIR(field_id="generic.flag_b", name="Flag B", canonical_name="flag_b", declared_bit_width=5, declared_bit_offset=3),
            FieldIR(field_id="generic.code", name="Code", canonical_name="code", declared_bit_width=8, declared_bit_offset=8),
            FieldIR(field_id="generic.length", name="Length", canonical_name="length", declared_bit_width=16, declared_bit_offset=16),
            FieldIR(field_id="generic.sequence", name="Sequence", canonical_name="sequence", declared_bit_width=32, declared_bit_offset=32),
        ],
    )

    normalized = normalize_message_ir(message_ir)
    containers, diagnostics = build_packed_containers(normalized)
    fields = {field.canonical_name: field for field in normalized.fields}

    assert diagnostics == []
    assert normalized.normalization_status == NormalizationStatus.READY
    assert normalized.layout_kind == "bitfield_packed"
    assert normalized.total_size_bits == 64
    assert normalized.total_size_bytes == 8
    assert normalized.normalized_field_order == ["flag_a", "flag_b", "code", "length", "sequence"]
    assert fields["flag_a"].resolved_bit_offset == 0
    assert fields["flag_b"].resolved_bit_offset == 3
    assert fields["code"].resolved_bit_offset == 8
    assert fields["length"].resolved_bit_offset == 16
    assert fields["sequence"].resolved_bit_offset == 32
    assert fields["flag_a"].bit_lsb_index == 29
    assert fields["flag_a"].bit_msb_index == 31
    assert len(containers) == 1
    assert containers[0].start_bit_offset == 0
    assert containers[0].size_bits == 32
    assert containers[0].field_names == ("flag_a", "flag_b", "code", "length")


def test_normalization_blocks_impossible_overlapping_packed_layout():
    message_ir = MessageIR(
        ir_id="overlap_fixture",
        protocol_name="fixture",
        canonical_name="overlap_fixture",
        display_name="Overlap Fixture",
        fields=[
            FieldIR(field_id="overlap.alpha", name="Alpha", canonical_name="alpha", declared_bit_width=4, declared_bit_offset=0),
            FieldIR(field_id="overlap.beta", name="Beta", canonical_name="beta", declared_bit_width=5, declared_bit_offset=3),
        ],
    )

    normalized = normalize_message_ir(message_ir)

    assert normalized.normalization_status == NormalizationStatus.BLOCKED
    assert any(item.code == "field_layout_overlap" for item in normalized.diagnostics)


def test_bfd_control_packet_mandatory_section_is_generated(tmp_path: Path):
    schema = _auth_only_schema()

    result = generate_code(schema, str(tmp_path))
    by_name = {item.canonical_name: item for item in result.generated_message_irs}

    assert "bfd_control_packet_mandatory" in by_name
    assert by_name["bfd_control_packet_mandatory"].normalization_status == NormalizationStatus.READY
    assert by_name["bfd_control_packet_mandatory"].layout_kind == "bitfield_packed"
    assert (tmp_path / "bfd_msg_bfd_control_packet_mandatory_section.h").exists()
    assert (tmp_path / "bfd_msg_bfd_control_packet_mandatory_section.c").exists()
    assert any(symbol["symbol"].endswith("_pack") for symbol in result.expected_symbols if "control_packet_mandatory" in symbol["symbol"])
    assert any(symbol["symbol"].endswith("_unpack") for symbol in result.expected_symbols if "control_packet_mandatory" in symbol["symbol"])
    assert any(symbol["symbol"].endswith("_validate") for symbol in result.expected_symbols if "control_packet_mandatory" in symbol["symbol"])


def test_bfd_control_packet_composite_is_ready_and_exposes_auth_tail_metadata():
    schema = _auth_only_schema()

    message_irs = lower_protocol_messages_to_message_ir(schema.protocol_name, schema.messages)
    by_name = {item.canonical_name: item for item in message_irs}

    assert "bfd_control_packet" in by_name
    control = by_name["bfd_control_packet"]
    assert control.normalization_status == NormalizationStatus.READY
    assert control.layout_kind == "composite"
    assert control.min_size_bits == 192
    assert control.max_size_bits == 416
    assert len(control.composite_tails) == 1

    tail = control.composite_tails[0]
    assert tail.presence_rule_id == "bfd_control_packet.auth_tail.presence"
    assert tail.total_length_field == "header.length"
    assert tail.fixed_prefix_bits == 192
    assert tail.start_bit_offset == 192
    assert tail.min_span_bits == 32
    assert tail.max_span_bits == 224
    assert tail.candidate_message_irs == [
        "bfd_auth_simple_password",
        "bfd_auth_keyed_md5",
        "bfd_auth_keyed_sha1",
    ]
    assert [case.selector_values for case in tail.dispatch_cases] == [[1], [2, 3], [4, 5]]


@pytest.mark.skipif(not _is_gcc_available(), reason="gcc not available")
def test_bfd_control_packet_mandatory_roundtrip_and_first_word_bytes(tmp_path: Path):
    schema = _auth_only_schema()
    generated_dir = tmp_path / "generated"
    generate_code(schema, str(generated_dir))

    _compile_and_run_harness(
        generated_dir,
        ["bfd_msg_bfd_control_packet_mandatory_section.c"],
        "test_bfd_control_roundtrip.c",
        r'''
#include <stdint.h>
#include "bfd_msg_bfd_control_packet_mandatory_section.h"

int main(void) {
    bfd_bfd_control_packet_mandatory_section input = {0};
    bfd_bfd_control_packet_mandatory_section decoded = {0};
    uint8_t buffer[24] = {0};
    int rc = 0;

    input.version = 1;
    input.diag = 2;
    input.state = 3;
    input.poll = 1;
    input.final = 0;
    input.control_plane_independent = 1;
    input.auth_present = 0;
    input.demand = 1;
    input.multipoint = 0;
    input.detect_mult = 5;
    input.length = 24;
    input.my_discriminator = 0x01020304u;
    input.your_discriminator = 0x05060708u;
    input.desired_min_tx_interval = 0x11121314u;
    input.required_min_rx_interval = 0x21222324u;
    input.required_min_echo_rx_interval = 0x31323334u;

    rc = bfd_bfd_control_packet_mandatory_section_validate(&input);
    if (rc != 0) {
        return 1;
    }
    rc = bfd_bfd_control_packet_mandatory_section_pack(&input, buffer, sizeof(buffer));
    if (rc != 24) {
        return 1;
    }
    if (buffer[0] != 0x22 || buffer[1] != 0xea || buffer[2] != 0x05 || buffer[3] != 0x18) {
        return 1;
    }
    rc = bfd_bfd_control_packet_mandatory_section_unpack(&decoded, buffer, sizeof(buffer));
    if (rc != 24) {
        return 1;
    }
    if (decoded.version != input.version || decoded.diag != input.diag || decoded.state != input.state) {
        return 1;
    }
    if (decoded.poll != input.poll || decoded.final != input.final) {
        return 1;
    }
    if (decoded.control_plane_independent != input.control_plane_independent) {
        return 1;
    }
    if (decoded.auth_present != input.auth_present || decoded.demand != input.demand || decoded.multipoint != input.multipoint) {
        return 1;
    }
    if (decoded.detect_mult != input.detect_mult || decoded.length != input.length) {
        return 1;
    }
    if (decoded.my_discriminator != input.my_discriminator || decoded.your_discriminator != input.your_discriminator) {
        return 1;
    }
    if (decoded.desired_min_tx_interval != input.desired_min_tx_interval) {
        return 1;
    }
    if (decoded.required_min_rx_interval != input.required_min_rx_interval) {
        return 1;
    }
    if (decoded.required_min_echo_rx_interval != input.required_min_echo_rx_interval) {
        return 1;
    }
    return 0;
}
''',
    )


@pytest.mark.skipif(not _is_gcc_available(), reason="gcc not available")
def test_bfd_control_packet_validate_rejects_out_of_range_bitfield_value(tmp_path: Path):
    schema = _auth_only_schema()
    generated_dir = tmp_path / "generated"
    generate_code(schema, str(generated_dir))

    _compile_and_run_harness(
        generated_dir,
        ["bfd_msg_bfd_control_packet_mandatory_section.c"],
        "test_bfd_control_invalid_bitfield.c",
        r'''
#include "bfd_msg_bfd_control_packet_mandatory_section.h"

int main(void) {
    bfd_bfd_control_packet_mandatory_section msg = {0};
    msg.version = 8;
    msg.length = 24;
    msg.auth_present = 0;
    if (bfd_bfd_control_packet_mandatory_section_validate(&msg) == 0) {
        return 1;
    }
    return 0;
}
''',
    )


@pytest.mark.skipif(not _is_gcc_available(), reason="gcc not available")
def test_bfd_control_packet_composite_roundtrip_without_auth_tail(tmp_path: Path):
    schema = _auth_only_schema()
    generated_dir = tmp_path / "generated"
    generate_code(schema, str(generated_dir))

    _compile_and_run_harness(
        generated_dir,
        _composite_source_names(),
        "test_bfd_control_composite_no_auth.c",
        r'''
#include <stdint.h>
#include "bfd_msg_bfd_control_packet.h"

int main(void) {
    bfd_bfd_control_packet input = {0};
    bfd_bfd_control_packet decoded = {0};
    uint8_t buffer[64] = {0};
    int rc = 0;

    input.version = 1;
    input.diag = 2;
    input.state = 3;
    input.poll = 1;
    input.final = 0;
    input.control_plane_independent = 1;
    input.auth_present = 0;
    input.demand = 1;
    input.multipoint = 0;
    input.detect_mult = 5;
    input.length = 24;
    input.my_discriminator = 0x01020304u;
    input.your_discriminator = 0x05060708u;
    input.desired_min_tx_interval = 0x11121314u;
    input.required_min_rx_interval = 0x21222324u;
    input.required_min_echo_rx_interval = 0x31323334u;
    input.authentication_tail_kind = bfd_bfd_control_packet_authentication_tail_kind_NONE;

    rc = bfd_bfd_control_packet_validate(&input);
    if (rc != 0) {
        return 1;
    }
    rc = bfd_bfd_control_packet_pack(&input, buffer, sizeof(buffer));
    if (rc != 24) {
        return 1;
    }
    if (buffer[0] != 0x22 || buffer[1] != 0xea || buffer[2] != 0x05 || buffer[3] != 0x18) {
        return 1;
    }
    rc = bfd_bfd_control_packet_unpack(&decoded, buffer, sizeof(buffer));
    if (rc != 24) {
        return 1;
    }
    if (decoded.authentication_tail_kind != bfd_bfd_control_packet_authentication_tail_kind_NONE) {
        return 1;
    }
    if (decoded.auth_present != 0 || decoded.length != 24) {
        return 1;
    }
    if (decoded.my_discriminator != input.my_discriminator || decoded.required_min_echo_rx_interval != input.required_min_echo_rx_interval) {
        return 1;
    }
    return 0;
}
''',
    )


@pytest.mark.skipif(not _is_gcc_available(), reason="gcc not available")
def test_bfd_control_packet_composite_roundtrip_with_simple_password_tail(tmp_path: Path):
    schema = _auth_only_schema()
    generated_dir = tmp_path / "generated"
    generate_code(schema, str(generated_dir))

    _compile_and_run_harness(
        generated_dir,
        _composite_source_names(),
        "test_bfd_control_composite_simple.c",
        r'''
#include <stdint.h>
#include "bfd_msg_bfd_control_packet.h"

int main(void) {
    bfd_bfd_control_packet input = {0};
    bfd_bfd_control_packet decoded = {0};
    uint8_t buffer[64] = {0};
    int rc = 0;

    input.version = 1;
    input.diag = 2;
    input.state = 3;
    input.poll = 1;
    input.final = 0;
    input.control_plane_independent = 1;
    input.auth_present = 1;
    input.demand = 1;
    input.multipoint = 0;
    input.detect_mult = 5;
    input.length = 28;
    input.my_discriminator = 0x01020304u;
    input.your_discriminator = 0x05060708u;
    input.desired_min_tx_interval = 0x11121314u;
    input.required_min_rx_interval = 0x21222324u;
    input.required_min_echo_rx_interval = 0x31323334u;
    input.authentication_tail_kind = bfd_bfd_control_packet_authentication_tail_kind_SIMPLE_PASSWORD;

    input.bfd_auth_simple_password.auth_type = 1;
    input.bfd_auth_simple_password.auth_len = 4;
    input.bfd_auth_simple_password.auth_key_id = 7;
    input.bfd_auth_simple_password.password_len = 1;
    input.bfd_auth_simple_password.password[0] = 0x42;

    rc = bfd_bfd_control_packet_validate(&input);
    if (rc != 0) {
        return 1;
    }
    rc = bfd_bfd_control_packet_pack(&input, buffer, sizeof(buffer));
    if (rc != 28) {
        return 1;
    }
    if (buffer[0] != 0x22 || buffer[1] != 0xee || buffer[2] != 0x05 || buffer[3] != 0x1c) {
        return 1;
    }
    if (buffer[24] != 1 || buffer[25] != 4 || buffer[26] != 7 || buffer[27] != 0x42) {
        return 1;
    }
    rc = bfd_bfd_control_packet_unpack(&decoded, buffer, sizeof(buffer));
    if (rc != 28) {
        return 1;
    }
    if (decoded.authentication_tail_kind != bfd_bfd_control_packet_authentication_tail_kind_SIMPLE_PASSWORD) {
        return 1;
    }
    if (decoded.bfd_auth_simple_password.auth_type != 1 || decoded.bfd_auth_simple_password.password_len != 1) {
        return 1;
    }
    if (decoded.bfd_auth_simple_password.password[0] != 0x42 || decoded.length != 28) {
        return 1;
    }
    return 0;
}
''',
    )


@pytest.mark.skipif(not _is_gcc_available(), reason="gcc not available")
def test_bfd_control_packet_composite_roundtrip_with_keyed_md5_tail(tmp_path: Path):
    schema = _auth_only_schema()
    generated_dir = tmp_path / "generated"
    generate_code(schema, str(generated_dir))

    _compile_and_run_harness(
        generated_dir,
        _composite_source_names(),
        "test_bfd_control_composite_md5.c",
        r'''
#include <stdint.h>
#include "bfd_msg_bfd_control_packet.h"

int main(void) {
    bfd_bfd_control_packet input = {0};
    bfd_bfd_control_packet decoded = {0};
    uint8_t buffer[80] = {0};
    int rc = 0;
    size_t idx = 0;

    input.version = 1;
    input.diag = 2;
    input.state = 3;
    input.poll = 1;
    input.final = 0;
    input.control_plane_independent = 1;
    input.auth_present = 1;
    input.demand = 1;
    input.multipoint = 0;
    input.detect_mult = 5;
    input.length = 48;
    input.my_discriminator = 0x01020304u;
    input.your_discriminator = 0x05060708u;
    input.desired_min_tx_interval = 0x11121314u;
    input.required_min_rx_interval = 0x21222324u;
    input.required_min_echo_rx_interval = 0x31323334u;
    input.authentication_tail_kind = bfd_bfd_control_packet_authentication_tail_kind_KEYED_MD5;

    input.bfd_auth_keyed_md5.auth_type = 2;
    input.bfd_auth_keyed_md5.auth_len = 24;
    input.bfd_auth_keyed_md5.auth_key_id = 9;
    input.bfd_auth_keyed_md5.reserved = 0;
    input.bfd_auth_keyed_md5.sequence_number = 0x01020304u;
    for (idx = 0; idx < 16; ++idx) {
        input.bfd_auth_keyed_md5.auth_key_digest[idx] = (uint8_t)(0xa0u + idx);
    }

    rc = bfd_bfd_control_packet_validate(&input);
    if (rc != 0) {
        return 1;
    }
    rc = bfd_bfd_control_packet_pack(&input, buffer, sizeof(buffer));
    if (rc != 48) {
        return 1;
    }
    if (buffer[24] != 2 || buffer[25] != 24 || buffer[26] != 9 || buffer[27] != 0) {
        return 1;
    }
    rc = bfd_bfd_control_packet_unpack(&decoded, buffer, sizeof(buffer));
    if (rc != 48) {
        return 1;
    }
    if (decoded.authentication_tail_kind != bfd_bfd_control_packet_authentication_tail_kind_KEYED_MD5) {
        return 1;
    }
    if (decoded.bfd_auth_keyed_md5.auth_type != 2 || decoded.bfd_auth_keyed_md5.auth_len != 24) {
        return 1;
    }
    if (decoded.bfd_auth_keyed_md5.sequence_number != 0x01020304u) {
        return 1;
    }
    if (decoded.bfd_auth_keyed_md5.auth_key_digest[15] != 0xafu) {
        return 1;
    }
    return 0;
}
''',
    )


@pytest.mark.skipif(not _is_gcc_available(), reason="gcc not available")
def test_bfd_control_packet_composite_validate_rejects_invalid_tail_combinations(tmp_path: Path):
    schema = _auth_only_schema()
    generated_dir = tmp_path / "generated"
    generate_code(schema, str(generated_dir))

    _compile_and_run_harness(
        generated_dir,
        _composite_source_names(),
        "test_bfd_control_composite_negative.c",
        r'''
#include "bfd_msg_bfd_control_packet.h"

static void init_header(bfd_bfd_control_packet *msg) {
    msg->version = 1;
    msg->diag = 2;
    msg->state = 3;
    msg->poll = 1;
    msg->final = 0;
    msg->control_plane_independent = 1;
    msg->auth_present = 1;
    msg->demand = 1;
    msg->multipoint = 0;
    msg->detect_mult = 5;
    msg->my_discriminator = 0x01020304u;
    msg->your_discriminator = 0x05060708u;
    msg->desired_min_tx_interval = 0x11121314u;
    msg->required_min_rx_interval = 0x21222324u;
    msg->required_min_echo_rx_interval = 0x31323334u;
    msg->authentication_tail_kind = bfd_bfd_control_packet_authentication_tail_kind_SIMPLE_PASSWORD;
    msg->bfd_auth_simple_password.auth_type = 1;
    msg->bfd_auth_simple_password.auth_len = 4;
    msg->bfd_auth_simple_password.auth_key_id = 7;
    msg->bfd_auth_simple_password.password_len = 1;
    msg->bfd_auth_simple_password.password[0] = 0x42;
}

int main(void) {
    bfd_bfd_control_packet msg = {0};

    init_header(&msg);
    msg.length = 24;
    if (bfd_bfd_control_packet_validate(&msg) == 0) {
        return 1;
    }

    init_header(&msg);
    msg.length = 29;
    if (bfd_bfd_control_packet_validate(&msg) == 0) {
        return 1;
    }

    init_header(&msg);
    msg.length = 28;
    msg.bfd_auth_simple_password.auth_type = 2;
    if (bfd_bfd_control_packet_validate(&msg) == 0) {
        return 1;
    }

    return 0;
}
''',
    )


def test_composite_tail_blocks_when_span_cannot_be_derived():
    candidate = MessageIR(
        ir_id="child",
        protocol_name="fixture",
        canonical_name="child",
        display_name="Child",
        normalization_status=NormalizationStatus.READY,
    )
    parent = MessageIR(
        ir_id="parent",
        protocol_name="fixture",
        canonical_name="parent",
        display_name="Parent",
        fields=[
            FieldIR(field_id="parent.auth_present", name="Auth Present", canonical_name="auth_present", declared_bit_width=8, declared_byte_offset=0),
            FieldIR(field_id="parent.length", name="Length", canonical_name="length", declared_bit_width=8, declared_byte_offset=1),
        ],
        sections=[
            SectionIR(
                section_id="parent.header",
                name="Header",
                canonical_name="header",
                kind="mandatory_section",
                declared_byte_offset=0,
                field_ids=["auth_present", "length"],
            ),
            SectionIR(
                section_id="parent.tail",
                name="Tail",
                canonical_name="tail",
                kind="optional_tail",
                declared_byte_offset=2,
                optional=True,
                presence_rule_ids=["parent.tail.presence"],
            ),
        ],
        presence_rules=[
            PresenceRule(
                rule_id="parent.tail.presence",
                target_kind="section",
                target_id="parent.tail",
                expression="header.auth_present == 1",
                depends_on_fields=["header.auth_present"],
            )
        ],
        composite_tails=[
            CompositeTailIR(
                slot_id="parent.tail.slot",
                section_id="parent.tail",
                name="Tail",
                optional=True,
                presence_rule_id="parent.tail.presence",
                selector_field="tail.auth_type",
                total_length_field="header.length",
                fixed_prefix_bits=16,
                candidate_message_irs=["child"],
                dispatch_cases=[
                    CompositeDispatchCaseIR(
                        case_id="parent.tail.case.0",
                        selector_values=[1],
                        message_ir_id="child",
                    )
                ],
            )
        ],
    )

    normalized = normalize_message_ir(parent, available_message_irs={"child": candidate})

    assert normalized.normalization_status == NormalizationStatus.BLOCKED
    assert any(item.code == "unknown_tail_span" for item in normalized.diagnostics)
