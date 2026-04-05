"""Tests for codegen display-name standardization."""

from __future__ import annotations

import re
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from src.extract.codegen import (
    _sanitize_c_identifier,
    generate_code,
    standardize_msg_name,
    standardize_sm_name,
)
from src.models import ProtocolField, ProtocolMessage, ProtocolSchema, ProtocolState, ProtocolStateMachine, ProtocolTransition


NAME_CHARS = st.characters(whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters=" -_()/§.")


@given(text=st.text(NAME_CHARS, min_size=0, max_size=40))
@settings(max_examples=100)
def test_standardized_names_remain_valid_c_identifiers(text: str):
    sm_identifier = _sanitize_c_identifier(standardize_sm_name(text))
    msg_identifier = _sanitize_c_identifier(standardize_msg_name(text))

    assert re.fullmatch(r"^[A-Za-z_][A-Za-z0-9_]*$", sm_identifier)
    assert re.fullmatch(r"^[A-Za-z_][A-Za-z0-9_]*$", msg_identifier)


def test_standardize_name_helpers_apply_expected_cleanup():
    assert standardize_sm_name("BFD Session State Machine (RFC 5880 §6.2 Overview)") == "BFD Session State Machine"
    assert standardize_msg_name("Generic BFD Control Packet Format") == "BFD Control Packet"
    assert (
        standardize_msg_name("BFD Authentication Section - Simple Password Authentication")
        == "BFD Auth - Simple Password Authentication"
    )


def test_generate_code_uses_display_names_but_preserves_schema_names(tmp_path: Path):
    schema = ProtocolSchema(
        protocol_name="rfc5880-BFD",
        source_document="rfc5880-BFD.pdf",
        state_machines=[
            ProtocolStateMachine(
                name="BFD Session State Machine (RFC 5880 §6.2 Overview)",
                states=[ProtocolState(name="Down", is_initial=True)],
            )
        ],
        messages=[
            ProtocolMessage(
                name="Generic BFD Control Packet Format",
                fields=[ProtocolField(name="Version", size_bits=3)],
            )
        ],
    )
    original_dump = schema.model_copy(deep=True).model_dump()

    result = generate_code(schema, str(tmp_path))
    file_names = {Path(path).name for path in result.files}

    assert "bfd_sm_bfd_session_state_machine.h" in file_names
    assert any(name.startswith("bfd_msg_bfd_control_packet") and name.endswith(".h") for name in file_names)
    assert schema.model_dump() == original_dump


def test_generate_code_suffixes_duplicate_state_machine_names(tmp_path: Path):
    schema = ProtocolSchema(
        protocol_name="rfc793-TCP",
        source_document="rfc793-TCP.pdf",
        state_machines=[
            ProtocolStateMachine(
                name="TCP Timeout Handling",
                states=[ProtocolState(name="ANY", is_initial=True), ProtocolState(name="CLOSED", is_final=True)],
                transitions=[
                    ProtocolTransition(
                        from_state="ANY",
                        to_state="CLOSED",
                        event="User timeout expires",
                    )
                ],
            ),
            ProtocolStateMachine(
                name="TCP Timeout Handling",
                states=[ProtocolState(name="TIME-WAIT", is_initial=True), ProtocolState(name="CLOSED", is_final=True)],
                transitions=[
                    ProtocolTransition(
                        from_state="TIME-WAIT",
                        to_state="CLOSED",
                        event="Time-wait timeout expires",
                    )
                ],
            ),
        ],
    )

    result = generate_code(schema, str(tmp_path))
    file_names = {Path(path).name for path in result.files}
    symbol_names = {item["symbol"] for item in result.expected_symbols}

    assert "tcp_sm_tcp_timeout_handling.h" in file_names
    assert "tcp_sm_tcp_timeout_handling_2.h" in file_names
    assert "tcp_tcp_timeout_handling_transition" in symbol_names
    assert "tcp_tcp_timeout_handling_2_transition" in symbol_names
