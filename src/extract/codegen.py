"""Code generation for extracted protocol schemas."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from src.extract.message_ir import (
    PackedContainerLayout,
    build_packed_containers,
    lower_protocol_messages_to_message_ir,
    ready_message_irs,
)
from src.extract.rule_dsl import render_rule_expression_as_c
from src.models import CompositeTailIR, FieldIR, MessageIR, NormalizationStatus, ProtocolField, ProtocolMessage, ProtocolSchema, ProtocolStateMachine


GENERATOR_NAME = "protocol-twin-codegen"
TEMPLATE_DIR = Path(__file__).with_name("templates")


@dataclass
class FieldTypeInfo:
    c_type: str
    array_len: int | None = None
    comment: str = ""

    def render_declaration(self, field_name: str) -> str:
        name = _to_lower_snake(field_name)
        if self.array_len is not None:
            decl = f"{self.c_type} {name}[{self.array_len}];"
        else:
            decl = f"{self.c_type} {name};"
        if self.comment:
            decl = f"{decl} {self.comment}"
        return decl


@dataclass
class CodegenResult:
    files: list[str] = field(default_factory=list)
    skipped_components: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    expected_symbols: list[dict] = field(default_factory=list)
    generated_msg_headers: list[str] = field(default_factory=list)
    generated_msgs: list[ProtocolMessage] = field(default_factory=list)
    generated_message_irs: list[MessageIR] = field(default_factory=list)


def _sanitize_c_identifier(name: str | None) -> str:
    text = (name or "").strip()
    text = re.sub(r"[\s\-.\/]+", "_", text)
    text = re.sub(r"[^0-9A-Za-z_]", "", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        return "_unnamed"
    if text[0].isdigit():
        text = f"_{text}"
    return text or "_unnamed"


def _to_upper_snake(name: str | None) -> str:
    return _sanitize_c_identifier(name).upper()


def _to_lower_snake(name: str | None) -> str:
    return _sanitize_c_identifier(name).lower()


def _protocol_prefix(protocol_name: str | None) -> str:
    raw_name = (protocol_name or "").strip()
    parts = [part.strip() for part in raw_name.split("-") if part.strip()]
    filtered = [part for part in parts if not re.fullmatch(r"rfc\d*", part, flags=re.IGNORECASE)]
    if not filtered:
        filtered = [raw_name] if raw_name else []
    normalized = [_to_lower_snake(part) for part in filtered]
    normalized = [part for part in normalized if part and part != "_unnamed"]
    return "_".join(normalized) or "proto"


def _collapse_display_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" -_/")


def standardize_sm_name(canonical_name: str) -> str:
    text = (canonical_name or "").strip()
    if not text:
        return canonical_name or ""
    text = re.sub(
        r"[\(（]\s*(?:rfc\s*\d+[\s§\d\.\-–—]*|§?[\d\.]+[\-–—]?[\d\.]*)(?:\s+(?:excerpt|overview|summary))?\s*[\)）]",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\b(?:excerpt|overview|summary)\b", " ", text, flags=re.IGNORECASE)
    text = text.replace("&", " ")
    text = _collapse_display_whitespace(text)
    return text or canonical_name


def standardize_msg_name(canonical_name: str) -> str:
    text = (canonical_name or "").strip()
    if not text:
        return canonical_name or ""
    text = re.sub(r"^\s*Generic\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"[\(（]\s*(?:rfc\s*\d+[\s§\d\.\-–—]*|§?[\d\.]+[\-–—]?[\d\.]*)\s*[\)）]",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\bFormat\b$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bAuthentication Section\b", "Auth", text, flags=re.IGNORECASE)
    text = _collapse_display_whitespace(text)
    return text or canonical_name


def _map_field_type(field: ProtocolField) -> FieldTypeInfo:
    size_bits = field.size_bits
    bit_comment = f"/* {field.name}: {size_bits} bits */"
    if size_bits is None:
        return FieldTypeInfo("uint32_t", None, "/* TODO: size unknown */")
    if size_bits == 8:
        return FieldTypeInfo("uint8_t")
    if size_bits == 16:
        return FieldTypeInfo("uint16_t")
    if size_bits == 32:
        return FieldTypeInfo("uint32_t")
    if size_bits == 64:
        return FieldTypeInfo("uint64_t")
    if size_bits < 8:
        return FieldTypeInfo("uint8_t", None, bit_comment)
    if size_bits < 16:
        return FieldTypeInfo("uint16_t", None, bit_comment)
    if size_bits < 32:
        return FieldTypeInfo("uint32_t", None, bit_comment)
    if size_bits < 64:
        return FieldTypeInfo("uint64_t", None, bit_comment)
    return FieldTypeInfo("uint8_t", math.ceil(size_bits / 8), bit_comment)


def _sort_schema(schema: ProtocolSchema) -> ProtocolSchema:
    sorted_schema = schema.model_copy(deep=True)
    sorted_schema.state_machines = sorted(sorted_schema.state_machines, key=lambda item: item.name)
    sorted_schema.messages = sorted(sorted_schema.messages, key=lambda item: item.name)
    for state_machine in sorted_schema.state_machines:
        state_machine.states = sorted(state_machine.states, key=lambda item: item.name)
        state_machine.transitions = sorted(
            state_machine.transitions,
            key=lambda item: (item.from_state, item.to_state, item.event),
        )
    return sorted_schema


def _build_expected_symbols(
    generated_sms: list[ProtocolStateMachine],
    generated_msgs: list[ProtocolMessage | MessageIR],
    protocol_prefix: str,
) -> list[dict]:
    symbols: list[dict] = []
    for state_machine in generated_sms:
        sm_name = _to_lower_snake(standardize_sm_name(state_machine.name))
        symbols.extend(
            [
                {
                    "symbol": f"{protocol_prefix}_{sm_name}_state",
                    "kind": "enum",
                    "source": state_machine.name,
                },
                {
                    "symbol": f"{protocol_prefix}_{sm_name}_event",
                    "kind": "enum",
                    "source": state_machine.name,
                },
                {
                    "symbol": f"{protocol_prefix}_{sm_name}_transition",
                    "kind": "function",
                    "source": state_machine.name,
                },
            ]
        )
    for message in generated_msgs:
        raw_name = message.display_name if isinstance(message, MessageIR) else message.name
        msg_name = _to_lower_snake(standardize_msg_name(raw_name))
        symbols.extend(
            [
                {
                    "symbol": f"{protocol_prefix}_{msg_name}",
                    "kind": "struct",
                    "source": raw_name,
                },
                {
                    "symbol": f"{protocol_prefix}_{msg_name}_pack",
                    "kind": "function",
                    "source": raw_name,
                },
                {
                    "symbol": f"{protocol_prefix}_{msg_name}_unpack",
                    "kind": "function",
                    "source": raw_name,
                },
                {
                    "symbol": f"{protocol_prefix}_{msg_name}_validate",
                    "kind": "function",
                    "source": raw_name,
                },
            ]
        )
    return symbols


def _load_templates() -> Environment:
    if not TEMPLATE_DIR.exists():
        raise FileNotFoundError(f"Template directory not found: {TEMPLATE_DIR}")
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters["upper_snake"] = _to_upper_snake
    env.filters["lower_snake"] = _to_lower_snake
    env.filters["sanitize_id"] = _sanitize_c_identifier
    env.filters["map_field_type"] = _map_field_type
    return env


def _header_name_for_state_machine(protocol_prefix: str, state_machine: ProtocolStateMachine) -> str:
    return f"{protocol_prefix}_sm_{_to_lower_snake(standardize_sm_name(state_machine.name))}.h"


def _source_name_for_state_machine(protocol_prefix: str, state_machine: ProtocolStateMachine) -> str:
    return f"{protocol_prefix}_sm_{_to_lower_snake(standardize_sm_name(state_machine.name))}.c"


def _header_name_for_message(protocol_prefix: str, message: ProtocolMessage | MessageIR) -> str:
    return f"{protocol_prefix}_msg_{_to_lower_snake(_message_display_name(message))}.h"


def _source_name_for_message(protocol_prefix: str, message: ProtocolMessage | MessageIR) -> str:
    return f"{protocol_prefix}_msg_{_to_lower_snake(_message_display_name(message))}.c"


def _enum_entries(kind_prefix: str, values: list[str], fallback: str) -> list[str]:
    if not values:
        return [f"{kind_prefix}_{fallback}"]
    return [f"{kind_prefix}_{_to_upper_snake(value)}" for value in values]


def _build_state_machine_context(
    protocol_prefix: str,
    schema: ProtocolSchema,
    state_machine: ProtocolStateMachine,
    header_name: str,
) -> dict:
    display_name = standardize_sm_name(state_machine.name)
    component_name = _to_lower_snake(display_name)
    symbol_prefix = f"{protocol_prefix}_{component_name}"
    state_names = [state.name for state in state_machine.states]
    event_names = sorted({transition.event for transition in state_machine.transitions if transition.event})
    state_entries = _enum_entries(f"{symbol_prefix}_STATE", state_names, "UNSPECIFIED")
    event_entries = _enum_entries(f"{symbol_prefix}_EVENT", event_names, "NONE")
    state_lookup = dict(zip(state_names, state_entries))
    event_lookup = dict(zip(event_names, event_entries))
    transitions = []
    for transition in state_machine.transitions:
        transitions.append(
            {
                "from_state": state_lookup.get(
                    transition.from_state,
                    f"{symbol_prefix}_STATE_{_to_upper_snake(transition.from_state or 'UNSPECIFIED')}",
                ),
                "to_state": state_lookup.get(
                    transition.to_state,
                    f"{symbol_prefix}_STATE_{_to_upper_snake(transition.to_state or 'UNSPECIFIED')}",
                ),
                "event": event_lookup.get(
                    transition.event,
                    f"{symbol_prefix}_EVENT_{_to_upper_snake(transition.event or 'NONE')}",
                ),
                "condition": transition.condition,
                "actions": list(transition.actions),
            }
        )
    return {
        "protocol_prefix": protocol_prefix,
        "component_name": component_name,
        "symbol_prefix": symbol_prefix,
        "state_enum": f"{symbol_prefix}_state",
        "event_enum": f"{symbol_prefix}_event",
        "function_name": f"{symbol_prefix}_transition",
        "states": [
            {
                "name": entry,
                "description": state.description,
                "is_initial": state.is_initial,
                "is_final": state.is_final,
            }
            for state, entry in zip(state_machine.states, state_entries)
        ]
        or [{"name": state_entries[0], "description": "", "is_initial": False, "is_final": False}],
        "events": event_entries,
        "transitions": transitions,
        "source_document": schema.source_document or schema.protocol_name,
        "generator_name": GENERATOR_NAME,
        "include_guard": f"{_to_upper_snake(header_name)}_H",
        "header_name": header_name,
        "state_machine_name": state_machine.name,
        "state_machine_display_name": display_name,
    }


def _field_member_name(field: FieldIR) -> str:
    return _to_lower_snake(field.canonical_name)


def _field_length_member_name(field: FieldIR) -> str:
    return f"{_field_member_name(field)}_len"


def _message_display_name(message: ProtocolMessage | MessageIR) -> str:
    raw_name = message.display_name if isinstance(message, MessageIR) else message.name
    return standardize_msg_name(raw_name)


def _message_component_name(message: ProtocolMessage | MessageIR) -> str:
    return _to_lower_snake(_message_display_name(message))


def _message_symbol_prefix(protocol_prefix: str, message: ProtocolMessage | MessageIR) -> str:
    return f"{protocol_prefix}_{_message_component_name(message)}"


def _message_to_protocol_message(message_ir: MessageIR) -> ProtocolMessage:
    fields: list[ProtocolField] = []
    ordered = {field.canonical_name: field for field in message_ir.fields}
    for field_name in message_ir.normalized_field_order:
        field = ordered.get(field_name)
        if field is None:
            continue
        fields.append(
            ProtocolField(
                name=field.name,
                type=field.storage_type or "",
                size_bits=field.declared_bit_width,
                description=field.description or "",
            )
        )
    return ProtocolMessage(
        name=message_ir.display_name,
        fields=fields,
        source_pages=list(message_ir.source_pages),
    )


def _message_size_expr(message_ir: MessageIR, prefix: str) -> str:
    if message_ir.composite_tails:
        total_length_field = message_ir.composite_tails[0].total_length_field
        if total_length_field:
            return f"((size_t){_field_ref_to_c(total_length_field, prefix)})"
    if message_ir.total_size_bytes is not None:
        return str(message_ir.total_size_bytes)
    variable_fields = [field for field in message_ir.fields if field.is_variable_length]
    if len(variable_fields) == 1 and variable_fields[0].length_from_field:
        return f"((size_t){_field_ref_to_c(variable_fields[0].length_from_field, prefix)})"
    if message_ir.max_size_bits is not None:
        return str(message_ir.max_size_bits // 8)
    raise ValueError(f"Unable to derive total message size for {message_ir.display_name}")


def _field_ref_to_c(field_ref: str, prefix: str = "msg->") -> str:
    member = field_ref.split(".")[-1]
    return f"{prefix}{_to_lower_snake(member)}"


def _storage_type_bits(storage_type: str | None) -> int | None:
    return {
        "uint8_t": 8,
        "uint16_t": 16,
        "uint32_t": 32,
        "uint64_t": 64,
    }.get(storage_type or "")


def _uint_literal(value: int) -> str:
    if value > 0xFFFFFFFF:
        return f"{value}ULL"
    if value > 0xFFFF:
        return f"{value}u"
    return str(value)


def _fixed_field_size_bytes(field: FieldIR) -> int | None:
    if field.is_variable_length:
        return None
    if field.is_array and field.array_len is not None:
        return field.array_len
    if field.resolved_bit_width is None or field.resolved_bit_width % 8 != 0:
        return None
    return field.resolved_bit_width // 8


def _render_write_container_bytes(container_var: str, container: PackedContainerLayout) -> list[str]:
    return [
        f"buf[{container.start_byte_offset + index}] = (uint8_t)(({container_var} >> {container.size_bits - ((index + 1) * 8)}) & 0xffu);"
        for index in range(container.size_bits // 8)
    ]


def _render_read_container_bytes(container_var: str, container: PackedContainerLayout) -> list[str]:
    lines = [f"uint64_t {container_var} = 0;"]
    for index in range(container.size_bits // 8):
        shift = container.size_bits - ((index + 1) * 8)
        lines.append(
            f"{container_var} |= ((uint64_t)buf[{container.start_byte_offset + index}] << {shift});"
        )
    return lines


def _struct_field_entries(field: FieldIR) -> list[dict[str, str]]:
    name = _field_member_name(field)
    description = field.description or ""
    if field.is_array:
        array_len = field.array_len or max(1, (_fixed_field_size_bytes(field) or 1))
        entries = [
            {
                "declaration": f"uint8_t {name}[{array_len}];",
                "description": description,
            }
        ]
        if field.is_variable_length:
            entries.append(
                {
                    "declaration": f"size_t {_field_length_member_name(field)};",
                    "description": f"runtime length for {field.name}",
                }
            )
        return entries
    declaration = f"{field.storage_type} {name};"
    return [{"declaration": declaration, "description": description}]


def _render_write_scalar(field: FieldIR, offset: int) -> list[str]:
    name = _field_member_name(field)
    if field.storage_type == "uint8_t":
        return [f"buf[{offset}] = (uint8_t)(msg->{name});"]
    if field.storage_type == "uint16_t":
        return [
            f"buf[{offset}] = (uint8_t)((msg->{name} >> 8) & 0xff);",
            f"buf[{offset + 1}] = (uint8_t)(msg->{name} & 0xff);",
        ]
    if field.storage_type == "uint32_t":
        return [
            f"buf[{offset}] = (uint8_t)((msg->{name} >> 24) & 0xff);",
            f"buf[{offset + 1}] = (uint8_t)((msg->{name} >> 16) & 0xff);",
            f"buf[{offset + 2}] = (uint8_t)((msg->{name} >> 8) & 0xff);",
            f"buf[{offset + 3}] = (uint8_t)(msg->{name} & 0xff);",
        ]
    if field.storage_type == "uint64_t":
        return [
            f"buf[{offset + idx}] = (uint8_t)((msg->{name} >> {56 - 8 * idx}) & 0xff);"
            for idx in range(8)
        ]
    raise ValueError(f"Unsupported scalar storage type for write: {field.storage_type}")


def _render_read_scalar(field: FieldIR, offset: int) -> list[str]:
    name = _field_member_name(field)
    if field.storage_type == "uint8_t":
        return [f"msg->{name} = (uint8_t)buf[{offset}];"]
    if field.storage_type == "uint16_t":
        return [f"msg->{name} = (uint16_t)(((uint16_t)buf[{offset}] << 8) | (uint16_t)buf[{offset + 1}]);"]
    if field.storage_type == "uint32_t":
        return [
            f"msg->{name} = ((uint32_t)buf[{offset}] << 24) | ((uint32_t)buf[{offset + 1}] << 16) | "
            f"((uint32_t)buf[{offset + 2}] << 8) | (uint32_t)buf[{offset + 3}];"
        ]
    if field.storage_type == "uint64_t":
        return [
            f"msg->{name} = ((uint64_t)buf[{offset}] << 56) | ((uint64_t)buf[{offset + 1}] << 48) | "
            f"((uint64_t)buf[{offset + 2}] << 40) | ((uint64_t)buf[{offset + 3}] << 32) | "
            f"((uint64_t)buf[{offset + 4}] << 24) | ((uint64_t)buf[{offset + 5}] << 16) | "
            f"((uint64_t)buf[{offset + 6}] << 8) | (uint64_t)buf[{offset + 7}];"
        ]
    raise ValueError(f"Unsupported scalar storage type for read: {field.storage_type}")


def _enum_values_for_field(message_ir: MessageIR, field: FieldIR) -> list[int]:
    if field.enum_domain_id is None:
        return []
    for domain in message_ir.enum_domains:
        if domain.enum_id == field.enum_domain_id:
            return [item.value for item in domain.values]
    return []


def _validation_checks(message_ir: MessageIR) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    seen: set[str] = set()
    ordered = {field.canonical_name: field for field in message_ir.fields}

    def _add(condition: str, description: str) -> None:
        if condition in seen:
            return
        seen.add(condition)
        checks.append({"condition": condition, "description": description})

    for field_name in message_ir.normalized_field_order:
        field = ordered[field_name]
        storage_bits = _storage_type_bits(field.storage_type)
        if (
            not field.is_array
            and storage_bits is not None
            and field.resolved_bit_width is not None
            and field.resolved_bit_width < storage_bits
        ):
            max_value = (1 << field.resolved_bit_width) - 1
            _add(
                f"({_field_ref_to_c(field.canonical_name)} <= {_uint_literal(max_value)})",
                f"{field.name} fits within {field.resolved_bit_width} wire bits",
            )
        if field.const_value is not None:
            _add(f"({_field_ref_to_c(field.canonical_name)} == {field.const_value})", f"{field.name} matches fixed value")
        enum_values = _enum_values_for_field(message_ir, field)
        if enum_values:
            enum_condition = "(" + " || ".join(
                f"({_field_ref_to_c(field.canonical_name)} == {value})" for value in enum_values
            ) + ")"
            _add(enum_condition, f"{field.name} is within allowed enum domain")
        if field.is_variable_length:
            len_member = _field_length_member_name(field)
            if field.array_len is not None:
                _add(f"(msg->{len_member} <= {field.array_len})", f"{field.name} length stays within the declared maximum")
            if field.length_from_field and field.resolved_byte_offset is not None:
                _add(
                    f"(({_field_ref_to_c(field.length_from_field)} >= {field.resolved_byte_offset}) && "
                    f"(msg->{len_member} == (size_t)({_field_ref_to_c(field.length_from_field)} - {field.resolved_byte_offset})))",
                    f"{field.name} runtime length matches the declared length field",
                )

    for rule in message_ir.validation_rules:
        _add(
            render_rule_expression_as_c(rule.expression, lambda ref: _field_ref_to_c(ref)),
            rule.description or rule.expression,
        )
    return checks


def _enum_contexts(message_ir: MessageIR, symbol_prefix: str) -> list[dict]:
    contexts: list[dict] = []
    for domain in message_ir.enum_domains:
        enum_name = f"{symbol_prefix}_{_to_lower_snake(domain.enum_id)}"
        values = []
        for item in domain.values:
            values.append(
                {
                    "name": f"{enum_name}_{_to_upper_snake(item.name)}",
                    "value": item.value,
                    "description": item.description or "",
                }
            )
        contexts.append({"enum_name": enum_name, "values": values})
    return contexts


def _candidate_ir_lookup(schema: ProtocolSchema) -> dict[str, MessageIR]:
    return {message_ir.canonical_name: message_ir for message_ir in _resolve_message_irs(schema)}


def _tail_kind_token(candidate_ir: MessageIR) -> str:
    return _to_upper_snake(candidate_ir.canonical_name.replace("bfd_auth_", "").replace("bfd_", ""))


def _tail_kind_symbol(symbol_prefix: str, candidate_ir: MessageIR) -> str:
    return f"{symbol_prefix}_{_tail_kind_token(candidate_ir)}"


def _message_struct_name(protocol_prefix: str, message_ir: MessageIR) -> str:
    return _message_symbol_prefix(protocol_prefix, message_ir)


def _message_pack_function(protocol_prefix: str, message_ir: MessageIR) -> str:
    return f"{_message_struct_name(protocol_prefix, message_ir)}_pack"


def _message_unpack_function(protocol_prefix: str, message_ir: MessageIR) -> str:
    return f"{_message_struct_name(protocol_prefix, message_ir)}_unpack"


def _message_validate_function(protocol_prefix: str, message_ir: MessageIR) -> str:
    return f"{_message_struct_name(protocol_prefix, message_ir)}_validate"


def _dispatch_selector_values(message_ir: MessageIR, values: list[int]) -> str:
    ordered = {field.canonical_name: field for field in message_ir.fields}
    for field_name in message_ir.normalized_field_order:
        field = ordered[field_name]
        enum_values = _enum_values_for_field(message_ir, field)
        if enum_values and any(value in enum_values for value in values):
            conditions = " || ".join(f"(auth_type == {value})" for value in values)
            return conditions
    return " || ".join(f"(auth_type == {value})" for value in values)


def _packed_container_lookup(message_ir: MessageIR) -> tuple[dict[str, PackedContainerLayout], list[PackedContainerLayout]]:
    containers, _ = build_packed_containers(message_ir)
    by_field: dict[str, PackedContainerLayout] = {}
    for container in containers:
        for field_name in container.field_names:
            by_field[field_name] = container
    return by_field, containers


def _pack_container_steps(message_ir: MessageIR, container: PackedContainerLayout) -> list[str]:
    fields_by_name = {field.canonical_name: field for field in message_ir.fields}
    container_var = f"packed_word_{container.start_byte_offset}"
    lines = [f"uint64_t {container_var} = 0;"]
    for packed_field in container.fields:
        field = fields_by_name[packed_field.canonical_name]
        member = _field_member_name(field)
        lines.append(
            f"{container_var} |= ((((uint64_t)msg->{member}) & {_uint_literal(packed_field.mask)}) << {packed_field.shift_bits});"
        )
    lines.extend(_render_write_container_bytes(container_var, container))
    return lines


def _unpack_container_steps(message_ir: MessageIR, container: PackedContainerLayout) -> list[str]:
    fields_by_name = {field.canonical_name: field for field in message_ir.fields}
    container_var = f"packed_word_{container.start_byte_offset}"
    lines = _render_read_container_bytes(container_var, container)
    for packed_field in container.fields:
        field = fields_by_name[packed_field.canonical_name]
        lines.append(
            f"msg->{_field_member_name(field)} = ({field.storage_type})(({container_var} >> {packed_field.shift_bits}) & {_uint_literal(packed_field.mask)});"
        )
    return lines


def _pack_steps(message_ir: MessageIR) -> list[str]:
    steps: list[str] = []
    ordered = {field.canonical_name: field for field in message_ir.fields}
    container_by_field, _ = _packed_container_lookup(message_ir)
    emitted_containers: set[str] = set()
    for field_name in message_ir.normalized_field_order:
        container = container_by_field.get(field_name)
        if container is not None:
            if container.container_id in emitted_containers:
                continue
            steps.extend(_pack_container_steps(message_ir, container))
            emitted_containers.add(container.container_id)
            continue
        field = ordered[field_name]
        if field.resolved_byte_offset is None:
            raise ValueError(f"Field {field.canonical_name} is missing resolved offset")
        offset = field.resolved_byte_offset
        if field.is_array:
            copy_len = (
                f"msg->{_field_length_member_name(field)}"
                if field.is_variable_length
                else str(field.array_len or _fixed_field_size_bytes(field) or 0)
            )
            steps.append(f"memcpy(buf + {offset}, msg->{_field_member_name(field)}, {copy_len});")
            continue
        steps.extend(_render_write_scalar(field, offset))
    return steps


def _unpack_steps(message_ir: MessageIR) -> list[str]:
    steps: list[str] = []
    ordered = {field.canonical_name: field for field in message_ir.fields}
    container_by_field, _ = _packed_container_lookup(message_ir)
    emitted_containers: set[str] = set()
    for field_name in message_ir.normalized_field_order:
        container = container_by_field.get(field_name)
        if container is not None:
            if container.container_id in emitted_containers:
                continue
            steps.extend(_unpack_container_steps(message_ir, container))
            emitted_containers.add(container.container_id)
            continue
        field = ordered[field_name]
        if field.resolved_byte_offset is None:
            raise ValueError(f"Field {field.canonical_name} is missing resolved offset")
        offset = field.resolved_byte_offset
        if field.is_array:
            if field.is_variable_length:
                if field.length_from_field is None or field.array_len is None:
                    raise ValueError(f"Variable-length field {field.canonical_name} is missing metadata")
                len_member = _field_length_member_name(field)
                length_expr = f"((size_t)({_field_ref_to_c(field.length_from_field)} - {offset}))"
                steps.append(f"if ({_field_ref_to_c(field.length_from_field)} < {offset}) return -1;")
                steps.append(f"msg->{len_member} = {length_expr};")
                steps.append(f"if (msg->{len_member} > {field.array_len}) return -1;")
                steps.append(f"if (buf_len < {offset} + msg->{len_member}) return -1;")
                steps.append(f"memcpy(msg->{_field_member_name(field)}, buf + {offset}, msg->{len_member});")
            else:
                array_len = field.array_len or _fixed_field_size_bytes(field) or 0
                steps.append(f"memcpy(msg->{_field_member_name(field)}, buf + {offset}, {array_len});")
            continue
        steps.extend(_render_read_scalar(field, offset))
    return steps


def _sample_value_assignment(field: FieldIR, message_ir: MessageIR) -> list[str]:
    name = _field_member_name(field)
    enum_values = _enum_values_for_field(message_ir, field)
    if field.is_array:
        lines: list[str] = []
        sample_len = min(field.array_len or 4, 4)
        if field.is_variable_length:
            len_member = _field_length_member_name(field)
            lines.append(f"input.{len_member} = {sample_len};")
            for idx in range(sample_len):
                lines.append(f"input.{name}[{idx}] = (uint8_t)({idx + 1});")
            if field.length_from_field and field.resolved_byte_offset is not None:
                length_member = _field_ref_to_c(field.length_from_field, prefix="input.")
                lines.append(f"{length_member} = (uint8_t)({field.resolved_byte_offset} + input.{len_member});")
            return lines
        for idx in range(sample_len):
            lines.append(f"input.{name}[{idx}] = (uint8_t)({idx + 1});")
        return lines
    if field.const_value is not None:
        return [f"input.{name} = {field.const_value};"]
    if enum_values:
        return [f"input.{name} = {enum_values[0]};"]
    storage_bits = _storage_type_bits(field.storage_type)
    if (
        storage_bits is not None
        and field.resolved_bit_width is not None
        and field.resolved_bit_width < storage_bits
    ):
        max_value = (1 << field.resolved_bit_width) - 1
        sample_value = min(max_value, 3)
        return [f"input.{name} = {sample_value};"]
    if field.storage_type == "uint8_t":
        return [f"input.{name} = 7;"]
    if field.storage_type == "uint16_t":
        return [f"input.{name} = 0x1234;"]
    if field.storage_type == "uint32_t":
        return [f"input.{name} = 0x12345678u;"]
    if field.storage_type == "uint64_t":
        return [f"input.{name} = 0x123456789abcdef0ULL;"]
    return []


def _roundtrip_assertions(message_ir: MessageIR) -> list[str]:
    assertions: list[str] = []
    ordered = {field.canonical_name: field for field in message_ir.fields}
    for field_name in message_ir.normalized_field_order:
        field = ordered[field_name]
        name = _field_member_name(field)
        if field.is_array:
            if field.is_variable_length:
                len_member = _field_length_member_name(field)
                assertions.append(f"if (decoded.{len_member} != input.{len_member}) return 1;")
                assertions.append(f"if (memcmp(decoded.{name}, input.{name}, input.{len_member}) != 0) return 1;")
            else:
                compare_len = field.array_len or _fixed_field_size_bytes(field) or 0
                assertions.append(f"if (memcmp(decoded.{name}, input.{name}, {compare_len}) != 0) return 1;")
            continue
        assertions.append(f"if (decoded.{name} != input.{name}) return 1;")
    return assertions


def _composite_tail_contexts(
    protocol_prefix: str,
    schema: ProtocolSchema,
    message_ir: MessageIR,
) -> list[dict]:
    registry = _candidate_ir_lookup(schema)
    contexts: list[dict] = []
    owner_symbol_prefix = _message_symbol_prefix(protocol_prefix, message_ir)
    for tail in message_ir.composite_tails:
        enum_name = f"{owner_symbol_prefix}_{_to_lower_snake(tail.name)}_kind"
        kind_member = f"{_to_lower_snake(tail.name)}_kind"
        slot_member = _to_lower_snake(tail.name)
        fixed_prefix_bytes = (tail.fixed_prefix_bits or 0) // 8
        candidates = []
        enum_values = [{"name": f"{enum_name}_NONE", "value": 0, "description": "tail absent"}]
        for index, case in enumerate(tail.dispatch_cases, start=1):
            candidate_ir = registry.get(case.message_ir_id)
            if candidate_ir is None:
                continue
            member_name = _to_lower_snake(case.message_ir_id)
            kind_symbol = _tail_kind_symbol(enum_name, candidate_ir)
            enum_values.append(
                {
                    "name": kind_symbol,
                    "value": index,
                    "description": case.description or candidate_ir.display_name,
                }
            )
            candidates.append(
                {
                    "kind_symbol": kind_symbol,
                    "member_name": member_name,
                    "struct_name": _message_struct_name(protocol_prefix, candidate_ir),
                    "header_name": _header_name_for_message(protocol_prefix, candidate_ir),
                    "pack_function": _message_pack_function(protocol_prefix, candidate_ir),
                    "unpack_function": _message_unpack_function(protocol_prefix, candidate_ir),
                    "validate_function": _message_validate_function(protocol_prefix, candidate_ir),
                    "selector_values": list(case.selector_values),
                    "selector_condition": " || ".join(f"(auth_type == {value})" for value in case.selector_values),
                    "size_expr": _message_size_expr(candidate_ir, f"msg->{member_name}."),
                    "size_expr_input": _message_size_expr(candidate_ir, f"input.{member_name}."),
                    "display_name": candidate_ir.display_name,
                }
            )
        contexts.append(
            {
                "slot": tail,
                "enum_name": enum_name,
                "enum_values": enum_values,
                "kind_member": kind_member,
                "fixed_prefix_bytes": fixed_prefix_bytes,
                "slot_member": slot_member,
                "candidates": candidates,
            }
        )
    return contexts


def _resolve_message_irs(schema: ProtocolSchema) -> list[MessageIR]:
    if schema.message_irs:
        return list(schema.message_irs)
    return lower_protocol_messages_to_message_ir(schema.protocol_name, schema.messages)


def _composite_extra_struct_fields(tail_contexts: list[dict]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for tail in tail_contexts:
        entries.append(
            {
                "declaration": f"{tail['enum_name']} {tail['kind_member']};",
                "description": f"host-side dispatch selector for {tail['slot'].name}",
            }
        )
        for candidate in tail["candidates"]:
            entries.append(
                {
                    "declaration": f"{candidate['struct_name']} {candidate['member_name']};",
                    "description": f"{candidate['display_name']} payload",
                }
            )
    return entries


def _composite_extra_headers(tail_contexts: list[dict]) -> list[str]:
    headers: list[str] = []
    for tail in tail_contexts:
        for candidate in tail["candidates"]:
            header_name = candidate["header_name"]
            if header_name not in headers:
                headers.append(header_name)
    return headers


def _composite_extra_enums(tail_contexts: list[dict]) -> list[dict]:
    return [{"enum_name": tail["enum_name"], "values": tail["enum_values"]} for tail in tail_contexts]


def _composite_validate_statements(tail_contexts: list[dict]) -> list[str]:
    statements: list[str] = []
    for tail in tail_contexts:
        prefix = tail["fixed_prefix_bytes"]
        kind_member = tail["kind_member"]
        none_symbol = f"{tail['enum_name']}_NONE"
        length_field = _field_ref_to_c(tail["slot"].total_length_field)
        presence_field = _field_ref_to_c("header.auth_present")
        statements.append(f"if ({presence_field} == 0) {{")
        statements.append(f"    if ({length_field} != {prefix}) return -1;")
        statements.append(f"    if (msg->{kind_member} != {none_symbol}) return -1;")
        statements.append("} else {")
        statements.append(f"    if ({length_field} <= {prefix}) return -1;")
        statements.append(f"    switch (msg->{kind_member}) {{")
        for candidate in tail["candidates"]:
            statements.append(f"    case {candidate['kind_symbol']}:")
            statements.append(f"        if ({candidate['validate_function']}(&msg->{candidate['member_name']}) != 0) return -1;")
            statements.append(f"        if ({length_field} != ({prefix} + {candidate['size_expr']})) return -1;")
            statements.append("        break;")
        statements.append("    default:")
        statements.append("        return -1;")
        statements.append("    }")
        statements.append("}")
    return statements


def _composite_pack_steps(tail_contexts: list[dict]) -> list[str]:
    steps: list[str] = []
    for tail in tail_contexts:
        prefix = tail["fixed_prefix_bytes"]
        kind_member = tail["kind_member"]
        none_symbol = f"{tail['enum_name']}_NONE"
        length_field = _field_ref_to_c(tail["slot"].total_length_field)
        presence_field = _field_ref_to_c("header.auth_present")
        steps.append(f"if ({presence_field} == 0) {{")
        steps.append(f"    if ({length_field} != {prefix}) return -1;")
        steps.append(f"    if (msg->{kind_member} != {none_symbol}) return -1;")
        steps.append("} else {")
        steps.append(f"    switch (msg->{kind_member}) {{")
        for candidate in tail["candidates"]:
            steps.append(f"    case {candidate['kind_symbol']}: {{")
            steps.append(
                f"        int tail_written = {candidate['pack_function']}(&msg->{candidate['member_name']}, buf + {prefix}, buf_len - {prefix});"
            )
            steps.append("        if (tail_written < 0) return -1;")
            steps.append(f"        if ({length_field} != (uint8_t)({prefix} + tail_written)) return -1;")
            steps.append("        break;")
            steps.append("    }")
        steps.append("    default:")
        steps.append("        return -1;")
        steps.append("    }")
        steps.append("}")
    return steps


def _composite_unpack_steps(tail_contexts: list[dict]) -> list[str]:
    steps: list[str] = []
    for tail in tail_contexts:
        prefix = tail["fixed_prefix_bytes"]
        kind_member = tail["kind_member"]
        none_symbol = f"{tail['enum_name']}_NONE"
        length_field = _field_ref_to_c(tail["slot"].total_length_field)
        presence_field = _field_ref_to_c("header.auth_present")
        steps.append(f"if ({presence_field} == 0) {{")
        steps.append(f"    if ({length_field} != {prefix}) return -1;")
        steps.append(f"    msg->{kind_member} = {none_symbol};")
        for candidate in tail["candidates"]:
            steps.append(f"    memset(&msg->{candidate['member_name']}, 0, sizeof(msg->{candidate['member_name']}));")
        steps.append("} else {")
        steps.append(f"    size_t tail_size = ((size_t){length_field}) - {prefix};")
        steps.append("    uint8_t auth_type = 0;")
        steps.append(f"    if ({length_field} <= {prefix}) return -1;")
        steps.append(f"    if (buf_len < (size_t){length_field}) return -1;")
        steps.append(f"    auth_type = buf[{prefix}];")
        for index, candidate in enumerate(tail["candidates"]):
            if index == 0:
                steps.append(f"    if ({candidate['selector_condition']}) {{")
            else:
                steps.append(f"    }} else if ({candidate['selector_condition']}) {{")
            steps.append(f"        msg->{kind_member} = {candidate['kind_symbol']};")
            steps.append(
                f"        if ({candidate['unpack_function']}(&msg->{candidate['member_name']}, buf + {prefix}, tail_size) != (int)tail_size) return -1;"
            )
        steps.append("    } else {")
        steps.append("        return -1;")
        steps.append("    }")
        steps.append("}")
    return steps


def _composite_roundtrip_setup(tail_contexts: list[dict]) -> list[str]:
    lines: list[str] = []
    for tail in tail_contexts:
        prefix = tail["fixed_prefix_bytes"]
        kind_member = tail["kind_member"]
        none_symbol = f"{tail['enum_name']}_NONE"
        lines.append(f"input.auth_present = 0;")
        lines.append(f"input.length = {prefix};")
        lines.append(f"input.{kind_member} = {none_symbol};")
    return lines


def _composite_roundtrip_assertions(tail_contexts: list[dict]) -> list[str]:
    assertions: list[str] = []
    for tail in tail_contexts:
        assertions.append(f"if (decoded.{tail['kind_member']} != input.{tail['kind_member']}) return 1;")
    return assertions


def _build_message_ir_context(
    protocol_prefix: str,
    schema: ProtocolSchema,
    message_ir: MessageIR,
    header_name: str,
) -> dict:
    display_name = _message_display_name(message_ir)
    component_name = _message_component_name(message_ir)
    symbol_prefix = _message_symbol_prefix(protocol_prefix, message_ir)
    ordered_lookup = {field.canonical_name: field for field in message_ir.fields}
    ordered_fields = [ordered_lookup[name] for name in message_ir.normalized_field_order if name in ordered_lookup]
    tail_contexts = _composite_tail_contexts(protocol_prefix, schema, message_ir)
    struct_fields = [entry for field in ordered_fields for entry in _struct_field_entries(field)]
    struct_fields.extend(_composite_extra_struct_fields(tail_contexts))
    variable_prefix_bytes = min(
        (field.resolved_byte_offset or 0 for field in ordered_fields if field.is_variable_length),
        default=None,
    )
    composite_prefix_bytes = min((tail["fixed_prefix_bytes"] for tail in tail_contexts), default=None)
    fixed_prefix_candidates = [value for value in (variable_prefix_bytes, composite_prefix_bytes) if value is not None]
    fixed_prefix_bytes = min(fixed_prefix_candidates) if fixed_prefix_candidates else None
    extra_headers = _composite_extra_headers(tail_contexts)
    enum_domains = _enum_contexts(message_ir, symbol_prefix) + _composite_extra_enums(tail_contexts)
    roundtrip_setup = [line for field in ordered_fields for line in _sample_value_assignment(field, message_ir)]
    roundtrip_setup.extend(_composite_roundtrip_setup(tail_contexts))
    return {
        "protocol_prefix": protocol_prefix,
        "component_name": component_name,
        "symbol_prefix": symbol_prefix,
        "struct_name": symbol_prefix,
        "pack_function": f"{symbol_prefix}_pack",
        "unpack_function": f"{symbol_prefix}_unpack",
        "validate_function": f"{symbol_prefix}_validate",
        "extra_headers": extra_headers,
        "struct_fields": struct_fields,
        "enum_domains": enum_domains,
        "pack_steps": _pack_steps(message_ir) + _composite_pack_steps(tail_contexts),
        "unpack_steps": _unpack_steps(message_ir) + _composite_unpack_steps(tail_contexts),
        "validate_checks": _validation_checks(message_ir),
        "validate_statements": _composite_validate_statements(tail_contexts),
        "roundtrip_setup": roundtrip_setup,
        "roundtrip_assertions": _roundtrip_assertions(message_ir) + _composite_roundtrip_assertions(tail_contexts),
        "source_document": schema.source_document or schema.protocol_name,
        "generator_name": GENERATOR_NAME,
        "include_guard": f"{_to_upper_snake(header_name)}_H",
        "header_name": header_name,
        "message_name": message_ir.display_name,
        "message_display_name": display_name,
        "total_size_expr": _message_size_expr(message_ir, "msg->"),
        "unpack_total_size_expr": _message_size_expr(message_ir, "msg->"),
        "fixed_total_size_bytes": message_ir.total_size_bytes,
        "fixed_prefix_bytes": fixed_prefix_bytes,
        "message_ir": message_ir,
    }


def _build_message_context(
    protocol_prefix: str,
    schema: ProtocolSchema,
    message: ProtocolMessage | MessageIR,
    header_name: str,
) -> dict:
    message_ir = message if isinstance(message, MessageIR) else None
    if message_ir is None:
        lowered = lower_protocol_messages_to_message_ir(schema.protocol_name, [message])
        ready = ready_message_irs(lowered)
        if not ready:
            raise ValueError(f"Message {message.name} is not READY for MessageIR codegen")
        message_ir = ready[0]
    if message_ir.normalization_status != NormalizationStatus.READY:
        raise ValueError(f"Message {message_ir.display_name} is not READY for MessageIR codegen")
    return _build_message_ir_context(protocol_prefix, schema, message_ir, header_name)


def _main_header_context(protocol_prefix: str, schema: ProtocolSchema, sub_headers: list[str], header_name: str) -> dict:
    return {
        "protocol_prefix": protocol_prefix,
        "protocol_name_upper": _to_upper_snake(schema.protocol_name),
        "include_guard": f"{_to_upper_snake(header_name)}_H",
        "sub_headers": sorted(sub_headers),
        "source_document": schema.source_document or schema.protocol_name,
        "generator_name": GENERATOR_NAME,
        "header_name": header_name,
    }


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _clear_generated_c_files(output_dir: Path) -> None:
    if not output_dir.exists():
        return
    for path in output_dir.iterdir():
        if path.is_file() and path.suffix in {".h", ".c"}:
            path.unlink()


def generate_code(schema: ProtocolSchema, output_dir: str) -> CodegenResult:
    sorted_schema = _sort_schema(schema)
    env = _load_templates()
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    _clear_generated_c_files(output_path)

    protocol_prefix = _protocol_prefix(sorted_schema.protocol_name)
    result = CodegenResult()
    generated_sms: list[ProtocolStateMachine] = []
    sub_headers: list[str] = []

    sm_h_template = env.get_template("state_machine.h.j2")
    sm_c_template = env.get_template("state_machine.c.j2")
    msg_h_template = env.get_template("message.h.j2")
    msg_c_template = env.get_template("message.c.j2")
    main_h_template = env.get_template("main_header.h.j2")

    for state_machine in sorted_schema.state_machines:
        try:
            header_name = _header_name_for_state_machine(protocol_prefix, state_machine)
            source_name = _source_name_for_state_machine(protocol_prefix, state_machine)
            context = _build_state_machine_context(protocol_prefix, sorted_schema, state_machine, header_name)
            header_path = output_path / header_name
            source_path = output_path / source_name
            _write_text(header_path, sm_h_template.render(**context))
            _write_text(source_path, sm_c_template.render(**context))
            result.files.extend([str(header_path), str(source_path)])
            sub_headers.append(header_name)
            generated_sms.append(state_machine)
        except Exception as exc:
            result.skipped_components.append(
                {"name": state_machine.name, "kind": "state_machine", "reason": str(exc)}
            )

    resolved_message_irs = _resolve_message_irs(sorted_schema)
    ready_irs = ready_message_irs(resolved_message_irs)
    for message_ir in resolved_message_irs:
        if message_ir.normalization_status == NormalizationStatus.READY:
            continue
        diagnostic_text = "; ".join(f"{diag.code}: {diag.message}" for diag in message_ir.diagnostics) or "not READY"
        result.warnings.append(f"{message_ir.display_name}: {diagnostic_text}")
        result.skipped_components.append(
            {
                "name": message_ir.display_name,
                "kind": "message",
                "reason": diagnostic_text,
            }
        )

    for message_ir in ready_irs:
        try:
            header_name = _header_name_for_message(protocol_prefix, message_ir)
            source_name = _source_name_for_message(protocol_prefix, message_ir)
            context = _build_message_context(protocol_prefix, sorted_schema, message_ir, header_name)
            header_path = output_path / header_name
            source_path = output_path / source_name
            _write_text(header_path, msg_h_template.render(**context))
            _write_text(source_path, msg_c_template.render(**context))
            result.files.extend([str(header_path), str(source_path)])
            sub_headers.append(header_name)
            result.generated_msg_headers.append(str(header_path))
            result.generated_message_irs.append(message_ir)
            result.generated_msgs.append(_message_to_protocol_message(message_ir))
        except Exception as exc:
            result.skipped_components.append(
                {"name": message_ir.display_name, "kind": "message", "reason": str(exc)}
            )
            result.warnings.append(f"{message_ir.display_name}: {exc}")

    main_header_name = f"{sorted_schema.protocol_name}.h"
    main_header_path = output_path / main_header_name
    _write_text(
        main_header_path,
        main_h_template.render(
            **_main_header_context(protocol_prefix, sorted_schema, sub_headers, main_header_name)
        ),
    )
    result.files.append(str(main_header_path))
    result.expected_symbols = _build_expected_symbols(generated_sms, result.generated_message_irs, protocol_prefix)
    return result
