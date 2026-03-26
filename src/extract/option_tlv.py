"""Minimal TCP option-list IR construction and parsing helpers."""

from __future__ import annotations

from src.extract.option_tlv_models import (
    OptionItemIR,
    OptionListDiagnostic,
    OptionListIR,
    OptionValueFieldIR,
    ParsedOptionItem,
    ParsedOptionList,
)


def build_tcp_option_list_ir(
    parent_message_ir_id: str,
    parent_section_id: str,
    span_expression: str,
    *,
    fixed_prefix_bytes: int = 20,
    max_size_bytes: int = 40,
) -> OptionListIR:
    list_id = f"{parent_message_ir_id}.options"
    return OptionListIR(
        list_id=list_id,
        name="TCP Options",
        canonical_name="tcp_options",
        parent_message_ir_id=parent_message_ir_id,
        parent_section_id=parent_section_id,
        span_expression=span_expression,
        fixed_prefix_bytes=fixed_prefix_bytes,
        min_size_bytes=0,
        max_size_bytes=max_size_bytes,
        item_kind="tcp_option",
        has_explicit_terminator=True,
        terminator_values=[0],
        alignment_bytes=4,
        padding_policy="unused_after_eol",
        fallback_mode="opaque_remainder",
        items=[
            OptionItemIR(
                item_id=f"{list_id}.eol",
                kind_value=0,
                kind_name="eol",
                length_model="singleton",
                fixed_size_bytes=1,
                min_size_bytes=1,
                max_size_bytes=1,
                is_terminal=True,
                description="End of option list",
            ),
            OptionItemIR(
                item_id=f"{list_id}.nop",
                kind_value=1,
                kind_name="nop",
                length_model="singleton",
                fixed_size_bytes=1,
                min_size_bytes=1,
                max_size_bytes=1,
                is_padding=True,
                description="No operation",
            ),
            OptionItemIR(
                item_id=f"{list_id}.mss",
                kind_value=2,
                kind_name="mss",
                length_model="kind_length_value",
                fixed_size_bytes=4,
                min_size_bytes=4,
                max_size_bytes=4,
                value_schema_kind="fixed_fields",
                value_fields=[
                    OptionValueFieldIR(
                        name="MSS Value",
                        canonical_name="mss_value",
                        width_bits=16,
                        byte_offset=2,
                        description="Maximum segment size value",
                    )
                ],
                description="Maximum Segment Size",
            ),
            OptionItemIR(
                item_id=f"{list_id}.window_scale",
                kind_value=3,
                kind_name="window_scale",
                length_model="kind_length_value",
                fixed_size_bytes=3,
                min_size_bytes=3,
                max_size_bytes=3,
                value_schema_kind="fixed_fields",
                value_fields=[
                    OptionValueFieldIR(
                        name="Shift Count",
                        canonical_name="shift_count",
                        width_bits=8,
                        byte_offset=2,
                        description="Window scale shift count",
                    )
                ],
                description="Window Scale",
            ),
        ],
    )


def parse_option_list_bytes(option_list: OptionListIR, data: bytes) -> ParsedOptionList:
    by_kind = {item.kind_value: item for item in option_list.items}
    parsed = ParsedOptionList()
    cursor = 0

    def _diag(level: str, code: str, message: str) -> None:
        parsed.diagnostics.append(
            OptionListDiagnostic(level=level, code=code, message=message)
        )

    while cursor < len(data):
        kind = data[cursor]
        item = by_kind.get(kind)
        if item is None:
            parsed.opaque_remainder = data[cursor:]
            parsed.fallback_triggered = True
            _diag("warning", "unknown_option_kind", f"Unsupported option kind {kind} fell back to opaque remainder.")
            return parsed

        if item.length_model == "singleton":
            parsed.items.append(
                ParsedOptionItem(
                    kind_value=kind,
                    kind_name=item.kind_name,
                    encoded_length=1,
                    raw_bytes=data[cursor : cursor + 1],
                    is_terminal=item.is_terminal,
                )
            )
            cursor += 1
            if item.is_terminal:
                parsed.terminated = True
                parsed.opaque_remainder = data[cursor:]
                parsed.fallback_triggered = len(parsed.opaque_remainder) > 0
                return parsed
            continue

        if cursor + 2 > len(data):
            parsed.fallback_triggered = True
            _diag("error", "truncated_option_length", f"Option kind {kind} is missing its length byte.")
            return parsed
        length = data[cursor + 1]
        if length < 2:
            parsed.fallback_triggered = True
            _diag("error", "invalid_option_length", f"Option kind {kind} has invalid length {length}.")
            return parsed
        if item.fixed_size_bytes is not None and length != item.fixed_size_bytes:
            parsed.fallback_triggered = True
            _diag(
                "error",
                "unexpected_option_length",
                f"Option kind {kind} expected total length {item.fixed_size_bytes}, got {length}.",
            )
            return parsed
        if cursor + length > len(data):
            parsed.fallback_triggered = True
            _diag("error", "option_out_of_bounds", f"Option kind {kind} exceeds the available span.")
            return parsed
        values: dict[str, int] = {}
        for field in item.value_fields:
            start = cursor + field.byte_offset
            size_bytes = field.width_bits // 8
            raw = data[start : start + size_bytes]
            if len(raw) != size_bytes:
                parsed.fallback_triggered = True
                _diag("error", "option_value_out_of_bounds", f"Option field {field.canonical_name} exceeds item bounds.")
                return parsed
            values[field.canonical_name] = int.from_bytes(raw, "big")
        parsed.items.append(
            ParsedOptionItem(
                kind_value=kind,
                kind_name=item.kind_name,
                encoded_length=length,
                values=values,
                raw_bytes=data[cursor : cursor + length],
            )
        )
        cursor += length

    return parsed
