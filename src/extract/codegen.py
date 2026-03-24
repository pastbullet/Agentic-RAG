"""Code generation for extracted protocol schemas."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from src.models import ProtocolField, ProtocolMessage, ProtocolSchema, ProtocolStateMachine


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
    generated_msgs: list[ProtocolMessage],
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
        msg_name = _to_lower_snake(standardize_msg_name(message.name))
        symbols.extend(
            [
                {
                    "symbol": f"{protocol_prefix}_{msg_name}",
                    "kind": "struct",
                    "source": message.name,
                },
                {
                    "symbol": f"{protocol_prefix}_{msg_name}_pack",
                    "kind": "function",
                    "source": message.name,
                },
                {
                    "symbol": f"{protocol_prefix}_{msg_name}_unpack",
                    "kind": "function",
                    "source": message.name,
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


def _header_name_for_message(protocol_prefix: str, message: ProtocolMessage) -> str:
    return f"{protocol_prefix}_msg_{_to_lower_snake(standardize_msg_name(message.name))}.h"


def _source_name_for_message(protocol_prefix: str, message: ProtocolMessage) -> str:
    return f"{protocol_prefix}_msg_{_to_lower_snake(standardize_msg_name(message.name))}.c"


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


def _build_message_context(
    protocol_prefix: str,
    schema: ProtocolSchema,
    message: ProtocolMessage,
    header_name: str,
) -> dict:
    display_name = standardize_msg_name(message.name)
    component_name = _to_lower_snake(display_name)
    symbol_prefix = f"{protocol_prefix}_{component_name}"
    fields = []
    for field in message.fields:
        type_info = _map_field_type(field)
        fields.append(
            {
                "name": _to_lower_snake(field.name),
                "type_info": type_info,
                "original_name": field.name,
                "size_bits": field.size_bits,
                "description": field.description,
            }
        )
    if not fields:
        fields.append(
            {
                "name": "_reserved",
                "type_info": FieldTypeInfo("uint8_t"),
                "original_name": "_reserved",
                "size_bits": 8,
                "description": "TODO: no fields extracted",
            }
        )
    return {
        "protocol_prefix": protocol_prefix,
        "component_name": component_name,
        "symbol_prefix": symbol_prefix,
        "struct_name": symbol_prefix,
        "pack_function": f"{symbol_prefix}_pack",
        "unpack_function": f"{symbol_prefix}_unpack",
        "fields": fields,
        "source_document": schema.source_document or schema.protocol_name,
        "generator_name": GENERATOR_NAME,
        "include_guard": f"{_to_upper_snake(header_name)}_H",
        "header_name": header_name,
        "message_name": message.name,
        "message_display_name": display_name,
    }


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

    for message in sorted_schema.messages:
        try:
            header_name = _header_name_for_message(protocol_prefix, message)
            source_name = _source_name_for_message(protocol_prefix, message)
            context = _build_message_context(protocol_prefix, sorted_schema, message, header_name)
            header_path = output_path / header_name
            source_path = output_path / source_name
            _write_text(header_path, msg_h_template.render(**context))
            _write_text(source_path, msg_c_template.render(**context))
            result.files.extend([str(header_path), str(source_path)])
            sub_headers.append(header_name)
            result.generated_msg_headers.append(str(header_path))
            result.generated_msgs.append(message)
        except Exception as exc:
            result.skipped_components.append(
                {"name": message.name, "kind": "message", "reason": str(exc)}
            )

    main_header_name = f"{sorted_schema.protocol_name}.h"
    main_header_path = output_path / main_header_name
    _write_text(
        main_header_path,
        main_h_template.render(
            **_main_header_context(protocol_prefix, sorted_schema, sub_headers, main_header_name)
        ),
    )
    result.files.append(str(main_header_path))
    result.expected_symbols = _build_expected_symbols(generated_sms, result.generated_msgs, protocol_prefix)
    return result
