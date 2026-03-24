"""Tests for code generation helpers and rendering."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.extract import codegen as codegen_module
from src.extract.codegen import (
    _build_expected_symbols,
    _map_field_type,
    _protocol_prefix,
    _sanitize_c_identifier,
    _sort_schema,
    _to_lower_snake,
    _to_upper_snake,
    generate_code,
)
from src.models import (
    ProtocolField,
    ProtocolMessage,
    ProtocolSchema,
    ProtocolState,
    ProtocolStateMachine,
    ProtocolTransition,
)


NAME_CHARS = st.characters(whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters=" -_")


def _non_empty_name_strategy(max_size: int = 24):
    return st.text(NAME_CHARS, min_size=1, max_size=max_size).filter(lambda text: text.strip() != "")


@st.composite
def protocol_field_strategy(draw):
    name = draw(_non_empty_name_strategy())
    size_bits = draw(st.one_of(st.none(), st.integers(min_value=1, max_value=128)))
    description = draw(st.text(st.characters(blacklist_characters="\x00"), max_size=40))
    field_type = draw(st.text(st.characters(whitelist_categories=("Ll", "Lu")), max_size=10))
    return ProtocolField(name=name, size_bits=size_bits, description=description, type=field_type)


@st.composite
def protocol_message_strategy(draw):
    name = draw(_non_empty_name_strategy())
    fields = draw(
        st.lists(
            protocol_field_strategy(),
            min_size=1,
            max_size=4,
            unique_by=lambda item: _to_lower_snake(item.name),
        )
    )
    return ProtocolMessage(name=name, fields=fields, source_pages=draw(st.lists(st.integers(1, 20), unique=True, max_size=4)))


@st.composite
def protocol_state_machine_strategy(draw):
    name = draw(_non_empty_name_strategy())
    state_names = draw(st.lists(_non_empty_name_strategy(12), min_size=1, max_size=4, unique_by=_to_lower_snake))
    states = [
        ProtocolState(name=state_name, is_initial=(idx == 0), is_final=(idx == len(state_names) - 1))
        for idx, state_name in enumerate(state_names)
    ]
    event_names = draw(st.lists(_non_empty_name_strategy(16), min_size=1, max_size=4, unique_by=_to_lower_snake))
    transition_count = draw(st.integers(min_value=0, max_value=4))
    transitions = []
    for _ in range(transition_count):
        from_state = draw(st.sampled_from(state_names))
        to_state = draw(st.sampled_from(state_names))
        event = draw(st.sampled_from(event_names))
        transitions.append(
            ProtocolTransition(
                from_state=from_state,
                to_state=to_state,
                event=event,
                condition=draw(st.text(max_size=20)),
                actions=draw(st.lists(st.text(max_size=20), max_size=2)),
            )
        )
    return ProtocolStateMachine(name=name, states=states, transitions=transitions)


@st.composite
def protocol_schema_strategy(draw):
    protocol_name = draw(_non_empty_name_strategy())
    state_machines = draw(
        st.lists(
            protocol_state_machine_strategy(),
            min_size=0,
            max_size=3,
            unique_by=lambda item: _to_lower_snake(item.name),
        )
    )
    messages = draw(
        st.lists(
            protocol_message_strategy(),
            min_size=0,
            max_size=3,
            unique_by=lambda item: _to_lower_snake(item.name),
        )
    )
    return ProtocolSchema(
        protocol_name=protocol_name,
        state_machines=state_machines,
        messages=messages,
        source_document=f"{_sanitize_c_identifier(protocol_name)}.pdf",
    )


# Feature: codegen-verify, Property 4: 标识符合法性
@given(text=_non_empty_name_strategy(30) | st.just("") | st.text(max_size=30))
@settings(max_examples=100)
def test_identifier_helpers_produce_valid_c_identifiers(text: str):
    sanitized = _sanitize_c_identifier(text)
    upper = _to_upper_snake(text)
    lower = _to_lower_snake(text)

    assert re.fullmatch(r"^[A-Za-z_][A-Za-z0-9_]*$", sanitized)
    assert re.fullmatch(r"^[A-Z_][A-Z0-9_]*$", upper)
    assert re.fullmatch(r"^[a-z_][a-z0-9_]*$", lower)


# Feature: codegen-verify, Property 5: 字段类型映射正确性
@given(field=protocol_field_strategy())
@settings(max_examples=100)
def test_map_field_type_matches_mapping_rules(field: ProtocolField):
    info = _map_field_type(field)
    size_bits = field.size_bits

    if size_bits is None:
        assert info.c_type == "uint32_t"
        assert info.comment == "/* TODO: size unknown */"
        assert info.array_len is None
    elif size_bits == 8:
        assert info.c_type == "uint8_t" and info.comment == ""
    elif size_bits == 16:
        assert info.c_type == "uint16_t" and info.comment == ""
    elif size_bits == 32:
        assert info.c_type == "uint32_t" and info.comment == ""
    elif size_bits == 64:
        assert info.c_type == "uint64_t" and info.comment == ""
    elif size_bits < 8:
        assert info.c_type == "uint8_t" and info.array_len is None
    elif size_bits < 16:
        assert info.c_type == "uint16_t" and info.array_len is None
    elif size_bits < 32:
        assert info.c_type == "uint32_t" and info.array_len is None
    elif size_bits < 64:
        assert info.c_type == "uint64_t" and info.array_len is None
    else:
        assert info.c_type == "uint8_t"
        assert info.array_len == (size_bits + 7) // 8

    declaration = info.render_declaration(field.name)
    assert declaration.startswith(info.c_type)
    assert ";" in declaration


# Feature: codegen-verify, Property 1: 确定性生成
@given(schema=protocol_schema_strategy())
@settings(max_examples=100)
def test_generate_code_is_deterministic(schema: ProtocolSchema):
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir_a, tempfile.TemporaryDirectory() as tmpdir_b:
        out_a = Path(tmpdir_a)
        out_b = Path(tmpdir_b)

        result_a = generate_code(schema, str(out_a))
        shuffled_schema = ProtocolSchema(
            protocol_name=schema.protocol_name,
            source_document=schema.source_document,
            state_machines=list(reversed(schema.state_machines)),
            messages=list(reversed(schema.messages)),
        )
        result_b = generate_code(shuffled_schema, str(out_b))

        files_a = {Path(path).name: Path(path).read_text(encoding="utf-8") for path in result_a.files}
        files_b = {Path(path).name: Path(path).read_text(encoding="utf-8") for path in result_b.files}
        assert files_a == files_b


# Feature: codegen-verify, Property 3: 结构完整性
@given(schema=protocol_schema_strategy())
@settings(max_examples=100)
def test_generate_code_emits_expected_symbols_and_files(schema: ProtocolSchema):
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        result = generate_code(schema, str(tmp_path))
        text = "\n".join(Path(path).read_text(encoding="utf-8") for path in result.files)

        for path in result.files:
            assert Path(path).exists()
        for symbol in result.expected_symbols:
            assert symbol["symbol"] in text

        protocol_prefix = _protocol_prefix(schema.protocol_name)
        main_header = tmp_path / f"{schema.protocol_name}.h"
        assert main_header.exists()
        for message_header in result.generated_msg_headers:
            assert Path(message_header).name in main_header.read_text(encoding="utf-8")
        rebuilt = _build_expected_symbols(
            _sort_schema(schema).state_machines,
            result.generated_msgs,
            protocol_prefix,
        )
        assert {item["symbol"] for item in result.expected_symbols}.issubset(
            {item["symbol"] for item in rebuilt}
        )


def test_generate_code_handles_empty_schema(tmp_path: Path):
    schema = ProtocolSchema(protocol_name="rfc5880-BFD", source_document="rfc5880-BFD.pdf")

    result = generate_code(schema, str(tmp_path))

    assert [Path(path).name for path in result.files] == ["rfc5880-BFD.h"]
    assert result.expected_symbols == []
    assert result.generated_msg_headers == []
    assert result.generated_msgs == []


def test_generate_code_renders_synthetic_bfd_schema(tmp_path: Path):
    schema = ProtocolSchema(
        protocol_name="rfc5880-BFD",
        source_document="rfc5880-BFD.pdf",
        state_machines=[
            ProtocolStateMachine(
                name="BFD Session",
                states=[
                    ProtocolState(name="Down", is_initial=True),
                    ProtocolState(name="Up", is_final=True),
                ],
                transitions=[
                    ProtocolTransition(
                        from_state="Down",
                        to_state="Up",
                        event="Receive Control Packet",
                        condition="packet valid",
                        actions=["start detection timer"],
                    )
                ],
            )
        ],
        messages=[
            ProtocolMessage(
                name="BFD Control Packet",
                fields=[
                    ProtocolField(name="Version", size_bits=3, description="protocol version"),
                    ProtocolField(name="Detect Mult", size_bits=8, description="detect multiplier"),
                ],
            )
        ],
    )

    result = generate_code(schema, str(tmp_path))

    assert len(result.files) == 5
    header_text = (tmp_path / "bfd_msg_bfd_control_packet_mandatory_section.h").read_text(encoding="utf-8")
    source_text = (tmp_path / "bfd_sm_bfd_session.c").read_text(encoding="utf-8")
    assert "typedef struct bfd_bfd_control_packet_mandatory_section" in header_text
    assert "bfd_bfd_control_packet_mandatory_section_pack" in header_text
    assert "return bfd_bfd_session_STATE_UP;" in source_text


def test_generate_code_raises_when_templates_are_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(codegen_module, "TEMPLATE_DIR", tmp_path / "missing-templates")
    schema = ProtocolSchema(protocol_name="proto", source_document="proto.pdf")

    with pytest.raises(FileNotFoundError):
        generate_code(schema, str(tmp_path / "generated"))
